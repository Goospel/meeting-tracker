"""CTER(Critical Token Error Rate) 엔티티 채점기 — 이 제품의 1순위 KPI.

골든 치명 토큰의 레퍼런스 스팬을 CER 정렬로 hypothesis에 투영해, 그 자리 표면을
canonical 값으로 정규화해 대조한다. 결과를 hit / value_mismatch(sub) / deleted(del) /
ambiguous로 분류하고, sub는 '가짜 모순 후보'(false_contradiction), del은 '놓친 모순
후보'(missed_token)로 분리 수집한다 — 둘은 제품 피해가 다르기 때문.

주의: 이 스코어러는 순수함수다(크레덴셜·오디오·IO 0). 화자귀속·역할스왑·통계
판정층은 v2로 미룬다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .cer import CerResult, align_ops, cer
from .entities import CriticalEntity, EntityType
from .korean_datetime import parse_date, parse_time
from .korean_numbers import NUM_CORE, currency_code, parse_number, unit_category
from .normalize import to_nfc

# 조사 — PROPER_NOUN 비교 전 후행 조사를 벗긴다 (재무팀과 → 재무팀).
_PARTICLES = sorted(
    ("으로", "로", "과의", "와의", "이랑", "한테", "에게", "에서", "에서는",
     "과", "와", "은", "는", "이", "가", "을", "를", "에", "의", "도", "만", "까지", "부터", "랑"),
    key=len,
    reverse=True,
)

_NUMERIC_TYPES = (
    EntityType.AMOUNT,
    EntityType.NUMBER,
    EntityType.PERCENT,
    EntityType.UNIT_QUANTITY,
)

# 숫자형 엔티티별 허용 단위 카테고리 — 밖이면 '무관 수 유입'으로 삭제 처리(R5).
_EXPECTED_CAT = {
    EntityType.AMOUNT: {None, "currency"},
    EntityType.PERCENT: {None, "percent"},
    EntityType.NUMBER: {None, "count", "currency", "percent"},
}

_NUMRUN = re.compile("[" + "".join(re.escape(c) for c in sorted(NUM_CORE)) + "]+")


@dataclass
class EntityScore:
    entity_id: str
    type: str
    outcome: str            # hit | value_mismatch | deleted | ambiguous
    ref_value: object = None
    hyp_value: object = None
    hyp_surface: str = ""


@dataclass
class Candidate:
    entity_id: str
    type: str
    ref_value: object = None
    hyp_value: object = None
    hyp_surface: str = ""


@dataclass
class TypeAgg:
    n: int = 0
    hit: int = 0
    sub: int = 0            # value_mismatch
    deleted: int = 0
    ambiguous: int = 0

    @property
    def sub_rate(self) -> float:
        return self.sub / self.n if self.n else 0.0

    @property
    def del_rate(self) -> float:
        return self.deleted / self.n if self.n else 0.0

    @property
    def cter(self) -> float:
        # 치명 토큰 오류율 = (치환 + 삭제) / n. ambiguous는 needs_review로 별도 노출(R15).
        return (self.sub + self.deleted) / self.n if self.n else 0.0

    @property
    def needs_review_rate(self) -> float:
        return self.ambiguous / self.n if self.n else 0.0


@dataclass
class ClipScore:
    cer: CerResult
    per_type: dict
    entity_scores: list
    false_contradiction_candidates: list = field(default_factory=list)
    missed_token_candidates: list = field(default_factory=list)


def _project_span(ops: list, hyp: str, cs: int, ce: int) -> str:
    """CER 정렬로 ref[cs:ce] 스팬을 hypothesis의 대응 구간으로 투영.

    스팬 경계에 인접한 삽입(ins)까지 포함한다(R10) — 값을 바꾸는 경계 삽입
    ('3천만'→'이삼천만')이 hit으로 통과하지 않도록.
    """
    touch = [k for k, (op, i, j) in enumerate(ops) if i is not None and cs <= i < ce]
    if not touch:
        return ""
    lo, hi = min(touch), max(touch)
    while lo - 1 >= 0 and ops[lo - 1][0] == "ins":
        lo -= 1
    while hi + 1 < len(ops) and ops[hi + 1][0] == "ins":
        hi += 1
    hyp_idxs = [j for (op, i, j) in ops[lo:hi + 1] if j is not None]
    if not hyp_idxs:
        return ""
    return hyp[min(hyp_idxs): max(hyp_idxs) + 1]


def _strip_particles(w: str) -> str:
    # 중첩 조사('에서는')까지 반복 제거 (S8).
    changed = True
    while changed:
        changed = False
        for p in _PARTICLES:
            if w.endswith(p) and len(w) > len(p):
                w = w[: -len(p)]
                changed = True
                break
    return w


def _unit_plausible(t: EntityType, unit: str | None) -> bool:
    exp = _EXPECTED_CAT.get(t)
    return True if exp is None else unit_category(unit) in exp


def _salvage_number(span: str, ent: CriticalEntity):
    """과대 스팬(F3)에서 파싱 가능한 최선의 수를 복구.

    후보를 NUM_CORE 연속 런으로 제한하고(효율·정확), (a) 엔티티 타입과 단위
    카테고리가 안 맞거나 (b) ref 값과 자릿수 스케일이 극단적으로 다른(>100배) 무관
    수는 배제한다(R5). 남은 것 중 수 문자를 가장 많이 담은 런을 채택.
    """
    ref_val = ent.canonical.get("value")
    if ref_val is None:
        ref_val = ent.canonical.get("low")
    best, best_core = None, 0
    for run in _NUMRUN.findall(span):
        r = parse_number(run)
        if r.kind not in ("value", "range"):
            continue
        if r.kind == "value":
            if not _unit_plausible(ent.type, r.unit):
                continue
            if ref_val and r.value and not (0.01 <= r.value / ref_val <= 100):
                continue   # 스케일 극단 차 → 무관 수(근처 시각·날짜 등)
        core = sum(1 for c in run if c in NUM_CORE)
        if core > best_core:
            best, best_core = r, core
    return best


def _gval(ent: CriticalEntity):
    c = ent.canonical
    if "value" in c:
        return c["value"]
    if "canonical" in c:
        return c["canonical"]
    if "low" in c:
        return (c.get("low"), c.get("high"))
    return c


def _classify(ent: CriticalEntity, span: str):
    """(outcome, hyp_value) 반환."""
    # 파서 밖 표기(manual opt-out, R12)는 채점 불가 → needs-review로.
    if ent.flags.get("manual"):
        return "ambiguous", None

    span = span.strip()
    if span == "":
        return "deleted", None

    t = ent.type

    if t in _NUMERIC_TYPES:
        r = parse_number(span)
        # 직접 파싱이 실패했거나(none) 선행 잡음을 값으로 읽어 단위가 부적합하면 salvage.
        if r.kind == "none" or (r.kind == "value" and not _unit_plausible(t, r.unit)):
            salv = _salvage_number(span, ent)
            if salv is not None:
                r = salv
        if r is None or r.kind == "none":
            return "deleted", None
        if r.kind in ("range", "ambiguous"):
            return "ambiguous", None
        if not _unit_plausible(t, r.unit):          # 무관 수 유입 (R5)
            return "deleted", None
        ok = (r.value == ent.canonical.get("value"))
        if t == EntityType.UNIT_QUANTITY:
            gu = ent.canonical.get("unit")
            ok = ok and (gu is None or r.unit == gu)
        elif t == EntityType.AMOUNT:                 # 통화 반전 (R4)
            gc, hc = ent.canonical.get("unit"), currency_code(r.unit)
            if gc and hc and hc != gc:
                ok = False
        return ("hit" if ok else "value_mismatch"), r.value

    if t == EntityType.RANGE:
        r = parse_number(span)
        if r.kind == "none":
            r = _salvage_number(span, ent)          # RANGE도 salvage (R11)
        if r is None or r.kind == "none":
            return "deleted", None
        if r.kind == "range":
            ok = r.low == ent.canonical.get("low") and r.high == ent.canonical.get("high")
            return ("hit" if ok else "value_mismatch"), (r.low, r.high)
        return "ambiguous", None                    # 범위 자리에 단일값 → needs-review

    if t in (EntityType.DATE, EntityType.TIME):
        parsed = (parse_date if t == EntityType.DATE else parse_time)(span)
        if not parsed:
            return "deleted", None
        gv = ent.canonical
        keys = set(gv) | set(parsed)                # 양방향 대칭 비교 (R3)
        ok = all(parsed.get(k) == gv.get(k) for k in keys)
        return ("hit" if ok else "value_mismatch"), parsed

    if t == EntityType.PROPER_NOUN:
        canon = ent.canonical.get("canonical", ent.surface)
        allowed = {canon} | set(ent.aliases)
        variants = {span, _strip_particles(span)}   # 조사 붙은 형/안 붙은 형 모두 허용 (F5)
        ok = bool(variants & allowed)
        return ("hit" if ok else "value_mismatch"), span

    return "ambiguous", None


def score_clip(ref_text: str, entities: list[CriticalEntity], hyp_text: str) -> ClipScore:
    """단일 세그먼트 채점: (레퍼런스 텍스트, 치명 토큰 주석, hypothesis 텍스트) → ClipScore."""
    ref, hyp = to_nfc(ref_text), to_nfc(hyp_text)
    result = cer(ref_text, hyp_text)
    ops = align_ops(list(ref), list(hyp))           # 세그먼트당 1회만 정렬 (S11)

    per_type: dict[str, TypeAgg] = {}
    escores: list[EntityScore] = []
    fcs: list[Candidate] = []
    mts: list[Candidate] = []

    for ent in entities:
        span = _project_span(ops, hyp, ent.char_start, ent.char_end)
        outcome, hv = _classify(ent, span)

        agg = per_type.setdefault(ent.type.value, TypeAgg())
        agg.n += 1
        if outcome == "hit":
            agg.hit += 1
        elif outcome == "value_mismatch":
            agg.sub += 1
        elif outcome == "deleted":
            agg.deleted += 1
        else:
            agg.ambiguous += 1

        gv = _gval(ent)
        escores.append(EntityScore(ent.entity_id, ent.type.value, outcome, gv, hv, span))
        if outcome == "value_mismatch":
            fcs.append(Candidate(ent.entity_id, ent.type.value, gv, hv, span))
        elif outcome == "deleted":
            mts.append(Candidate(ent.entity_id, ent.type.value, gv, None, span))

    return ClipScore(result, per_type, escores, fcs, mts)

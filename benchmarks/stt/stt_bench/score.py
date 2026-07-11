"""CTER(Critical Token Error Rate) 엔티티 채점기 — 이 제품의 1순위 KPI.

골든 치명 토큰의 레퍼런스 스팬을 CER 정렬로 hypothesis에 투영해, 그 자리 표면을
canonical 값으로 정규화해 대조한다. 결과를 hit / value_mismatch(sub) / deleted(del) /
ambiguous로 분류하고, sub는 '가짜 모순 후보'(false_contradiction), del은 '놓친 모순
후보'(missed_token)로 분리 수집한다 — 둘은 제품 피해가 다르기 때문.

주의: 이 스코어러는 순수함수다(크레덴셜·오디오·IO 0). 화자귀속·역할스왑·통계
판정층은 v2로 미룬다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cer import CerResult, align_ops, cer
from .entities import CriticalEntity, EntityType
from .korean_datetime import parse_date, parse_time
from .korean_numbers import parse_number
from .normalize import to_nfc

# 조사 — PROPER_NOUN 비교 전 후행 조사를 벗긴다 (재무팀과 → 재무팀).
_PARTICLES = sorted(
    ("으로", "로", "과의", "와의", "이랑", "한테", "에게", "에서",
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
        # 치명 토큰 오류율 = (치환 + 삭제) / n. type_confusion은 v2.
        return (self.sub + self.deleted) / self.n if self.n else 0.0


@dataclass
class ClipScore:
    cer: CerResult
    per_type: dict
    entity_scores: list
    false_contradiction_candidates: list = field(default_factory=list)
    missed_token_candidates: list = field(default_factory=list)


def _project_span(ref: str, hyp: str, cs: int, ce: int) -> str:
    """CER 정렬로 ref[cs:ce] 스팬을 hypothesis의 대응 구간으로 투영."""
    ops = align_ops(list(ref), list(hyp))
    ref_to_hyp = {i: j for op, i, j in ops if op in ("match", "sub")}
    hyp_idxs = [ref_to_hyp[i] for i in range(cs, ce) if i in ref_to_hyp]
    if not hyp_idxs:
        return ""  # 스팬 전체가 삭제됨
    return hyp[min(hyp_idxs): max(hyp_idxs) + 1]


def _strip_particles(w: str) -> str:
    for p in _PARTICLES:
        if w.endswith(p) and len(w) > len(p):
            return w[: -len(p)]
    return w


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
    span = span.strip()
    if span == "":
        return "deleted", None

    t = ent.type

    if t in _NUMERIC_TYPES:
        r = parse_number(span)
        if r.kind == "none":
            return "deleted", None            # 값이 뭉개져 사라짐 → 놓친 모순
        if r.kind in ("range", "ambiguous"):
            return "ambiguous", None          # 정규화기 구멍 아님 — needs-review
        gv = ent.canonical.get("value")
        ok = r.value == gv
        if t == EntityType.UNIT_QUANTITY:
            gu = ent.canonical.get("unit")
            ok = ok and (gu is None or r.unit == gu)
        return ("hit" if ok else "value_mismatch"), r.value

    if t == EntityType.RANGE:
        r = parse_number(span)
        if r.kind == "none":
            return "deleted", None
        if r.kind == "range":
            ok = r.low == ent.canonical.get("low") and r.high == ent.canonical.get("high")
            return ("hit" if ok else "value_mismatch"), (r.low, r.high)
        return "ambiguous", None

    if t in (EntityType.DATE, EntityType.TIME):
        parsed = (parse_date if t == EntityType.DATE else parse_time)(span)
        if not parsed:
            return "deleted", None
        gv = ent.canonical
        ok = all(parsed.get(k) == gv.get(k) for k in gv)
        return ("hit" if ok else "value_mismatch"), parsed

    if t == EntityType.PROPER_NOUN:
        core = _strip_particles(span)
        canon = ent.canonical.get("canonical", ent.surface)
        ok = core == canon or core in set(ent.aliases)
        return ("hit" if ok else "value_mismatch"), core

    return "ambiguous", None


def score_clip(ref_text: str, entities: list[CriticalEntity], hyp_text: str) -> ClipScore:
    """단일 세그먼트 채점: (레퍼런스 텍스트, 치명 토큰 주석, hypothesis 텍스트) → ClipScore."""
    ref, hyp = to_nfc(ref_text), to_nfc(hyp_text)
    result = cer(ref_text, hyp_text)

    per_type: dict[str, TypeAgg] = {}
    escores: list[EntityScore] = []
    fcs: list[Candidate] = []
    mts: list[Candidate] = []

    for ent in entities:
        span = _project_span(ref, hyp, ent.char_start, ent.char_end)
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

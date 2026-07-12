"""감지 채점기 — 예측 flag를 골든 flag에 매칭해 per-type P/R/F1 + 실패모드 분리.

객체탐지 평가와 같은 구조다: 각 flag을 그 statement 인용이 grounding되는 **전사 세그먼트
집합**으로 대표하고, 예측↔골든을 (같은 type + 세그먼트집합 Jaccard ≥ 임계)로 **그리디
1:1 매칭**한다.
  - 매칭 = TP
  - 미매칭 골든 = 놓친(FN) — 제품상 '놓친 모순'
  - 미매칭 예측 = 가짜(FP) — 제품상 '가짜 모순'. grounding 실패(할루시 인용)와
    grounding됐지만 골든에 없음을 분리(reason).
type-무관 localization을 따로 매칭해, 흐름단절은 찾았는데 라벨만 틀린 경우를
type_confusion으로 분리 노출한다(모순↔번복 혼동 등).

순수함수 · 런타임 의존성 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .grounding import resolve_flag_segments
from .labels import FlagType

_MATCH_THRESHOLD = 0.5


@dataclass
class PRF:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 0.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class FalsePositive:
    flag_id: str
    type: str
    reason: str                 # "ungrounded"(할루시 인용) | "unmatched"(골든에 없음)
    segments: tuple = ()
    ungrounded_quotes: tuple = ()


@dataclass
class Miss:
    flag_id: str
    type: str
    segments: tuple = ()
    type_confused: bool = False  # localization으론 찾았으나 라벨이 틀림


@dataclass
class TypeConfusion:
    golden_flag_id: str
    pred_flag_id: str
    golden_type: str
    pred_type: str
    segments: tuple = ()


@dataclass
class DetectionScore:
    per_type: dict
    overall: PRF
    localization: PRF
    matches: list = field(default_factory=list)          # (golden_id, pred_id) type-strict TP
    false_positives: list = field(default_factory=list)
    misses: list = field(default_factory=list)
    type_confusions: list = field(default_factory=list)


def _jaccard(a: frozenset, b: frozenset) -> float:
    u = a | b
    return len(a & b) / len(u) if u else 0.0


def _greedy_match(golds: list, preds: list, *, same_type: bool, thresh: float,
                  exclude_g: frozenset = frozenset(), exclude_p: frozenset = frozenset()):
    """(golds, preds) 각 원소 = (id, type, segs, ...). → 매칭된 (gi, pi) 인덱스 쌍 목록.

    후보 쌍을 Jaccard 내림차순(동점은 인덱스순)으로 정렬해 결정적으로 1:1 배정.
    exclude_g/exclude_p로 이미 배정된 인덱스를 후보에서 제외(localization을 strict 확장으로
    쌓을 때 사용).
    """
    pairs = []
    for gi, (_, gt, gs, *_) in enumerate(golds):
        if gi in exclude_g:
            continue
        for pi, (_, pt, ps, *_) in enumerate(preds):
            if pi in exclude_p or not ps:           # ungrounded 예측은 매칭 불가
                continue
            if same_type and gt != pt:
                continue
            j = _jaccard(gs, ps)
            if j >= thresh:
                pairs.append((j, gi, pi))
    pairs.sort(key=lambda x: (-x[0], x[1], x[2]))
    used_g, used_p, matched = set(), set(), []
    for _, gi, pi in pairs:
        if gi in used_g or pi in used_p:
            continue
        used_g.add(gi)
        used_p.add(pi)
        matched.append((gi, pi))
    return matched


def score_detection(golden_meeting: dict, pred_flags: list, *,
                    match_threshold: float = _MATCH_THRESHOLD) -> DetectionScore:
    transcript = golden_meeting["transcript"]

    golds = []
    for gf in golden_meeting["flags"]:
        segs, _ = resolve_flag_segments(gf, transcript)
        if not segs:                                # grounding 0 → 조용한 FN 강등 대신 에러
            raise ValueError(
                f"골든 flag {gf.flag_id}이 전사에 grounding되지 않음 — validate_golden을 먼저 통과시키세요"
            )
        golds.append((gf.flag_id, gf.type.value, segs))

    preds = []                                      # (id, type, segs, ungrounded) — 인덱스로만 참조(중복 id 안전)
    for pf in pred_flags:
        segs, ungrounded = resolve_flag_segments(pf, transcript)
        preds.append((pf.flag_id, pf.type.value, segs, tuple(ungrounded)))

    # 1) type-strict 그리디 → 정타(TP) 고정.
    strict = _greedy_match(golds, preds, same_type=True, thresh=match_threshold)
    matched_g = {gi for gi, _ in strict}
    matched_p = {pi for _, pi in strict}
    matches = [(golds[gi][0], preds[pi][0]) for gi, pi in strict]

    # 2) localization = strict 확장 — 남은 것끼리만 type-무관 매칭을 얹는다.
    #    (같은 type 남은 쌍은 strict에서 이미 매칭됐을 것이므로 여기 추가분은 전부 type 불일치.)
    extra = _greedy_match(golds, preds, same_type=False, thresh=match_threshold,
                          exclude_g=frozenset(matched_g), exclude_p=frozenset(matched_p))
    loc_matched_g = matched_g | {gi for gi, _ in extra}
    loc_matched_p = matched_p | {pi for _, pi in extra}
    localization = PRF(tp=len(strict) + len(extra),
                       fp=len(preds) - len(loc_matched_p),
                       fn=len(golds) - len(loc_matched_g))

    # type_confusion = 확장분(strict 정타는 애초에 제외됨) 중 type 불일치.
    type_confusions, confused_g = [], set()
    for gi, pi in extra:
        if golds[gi][1] != preds[pi][1]:
            confused_g.add(gi)
            type_confusions.append(TypeConfusion(
                golden_flag_id=golds[gi][0], pred_flag_id=preds[pi][0],
                golden_type=golds[gi][1], pred_type=preds[pi][1],
                segments=tuple(sorted(golds[gi][2])),
            ))

    # per-type: 골든 유형별 tp/fn + 예측 유형별 fp
    per_type = {t.value: PRF() for t in FlagType}
    for gi, (_, gt, _) in enumerate(golds):
        if gi in matched_g:
            per_type[gt].tp += 1
        else:
            per_type[gt].fn += 1
    for pi, (_, pt, _, _) in enumerate(preds):
        if pi not in matched_p:
            per_type[pt].fp += 1

    overall = PRF(
        tp=sum(v.tp for v in per_type.values()),
        fp=sum(v.fp for v in per_type.values()),
        fn=sum(v.fn for v in per_type.values()),
    )

    misses = [
        Miss(gid, gt, tuple(sorted(gs)), type_confused=(gi in confused_g))
        for gi, (gid, gt, gs) in enumerate(golds) if gi not in matched_g
    ]
    false_positives = []
    for pi, (pid, pt, ps, ung) in enumerate(preds):
        if pi in matched_p:
            continue
        false_positives.append(FalsePositive(
            flag_id=pid, type=pt,
            reason="ungrounded" if not ps else "unmatched",
            segments=tuple(sorted(ps)), ungrounded_quotes=ung,
        ))

    return DetectionScore(
        per_type=per_type, overall=overall, localization=localization,
        matches=matches, false_positives=false_positives,
        misses=misses, type_confusions=type_confusions,
    )

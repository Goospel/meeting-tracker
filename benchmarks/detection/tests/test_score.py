"""감지 채점 하네스 — 그리디 매칭 · per-type P/R/F1 · 가짜(FP)/놓친(FN) 분리 · type_confusion.

객체탐지식 평가: 예측 flag ↔ 골든 flag를 (같은 type + 세그먼트집합 Jaccard≥임계)로 1:1 매칭.
매칭=TP / 미매칭 골든=놓친(FN) / 미매칭 예측=가짜(FP). type-무관 localization으로 라벨만
틀린 경우(type_confusion)를 분리 노출.
"""

import json
from pathlib import Path

import pytest

from detect_bench.labels import (
    FlagType,
    FlowFlag,
    Statement,
    TranscriptSegment,
    load_meeting,
    load_pred_flags,
)
from detect_bench.score import score_detection

FIX = Path(__file__).resolve().parent.parent / "fixtures"
GOLDEN = FIX / "golden" / "luma_meeting.json"
FAITHFUL = FIX / "pred" / "luma_meeting.faithful.json"
CONTAMINATED = FIX / "pred" / "luma_meeting.contaminated.json"


def _golden():
    return load_meeting(GOLDEN)


# ── 충실한 예측: 완벽 재현 ─────────────────────────────────────────────────

def test_faithful_prediction_scores_perfect():
    s = score_detection(_golden(), load_pred_flags(FAITHFUL))
    assert s.overall.tp == 4 and s.overall.fp == 0 and s.overall.fn == 0
    assert s.overall.precision == 1.0 and s.overall.recall == 1.0 and s.overall.f1 == 1.0
    for t in ("모순", "번복", "미해결", "재논의"):
        assert s.per_type[t].recall == 1.0
    assert s.misses == [] and s.false_positives == [] and s.type_confusions == []
    assert s.localization.tp == 4


# ── 오염된 예측: 4가지 실패모드 분리 집계 ──────────────────────────────────

def test_contaminated_overall_counts():
    s = score_detection(_golden(), load_pred_flags(CONTAMINATED))
    # 정타 2(모순 c1, 재논의 c4), 가짜 3(c2 오타입, hallu1, spurious1), 놓친 2(번복 f2, 미해결 f3)
    assert (s.overall.tp, s.overall.fp, s.overall.fn) == (2, 3, 2)
    assert s.overall.precision == 2 / 5
    assert s.overall.recall == 2 / 4


def test_contaminated_per_type():
    pt = score_detection(_golden(), load_pred_flags(CONTAMINATED)).per_type
    assert (pt["모순"].tp, pt["모순"].fp, pt["모순"].fn) == (1, 2, 0)   # c1 정타, c2+hallu1 가짜
    assert (pt["번복"].tp, pt["번복"].fn) == (0, 1)                     # f2 놓침(타입혼동으로)
    assert (pt["미해결"].tp, pt["미해결"].fp, pt["미해결"].fn) == (0, 1, 1)  # spurious1 가짜, f3 놓침
    assert (pt["재논의"].tp, pt["재논의"].fn) == (1, 0)                  # c4 정타


def test_contaminated_localization_recovers_type_confusion():
    s = score_detection(_golden(), load_pred_flags(CONTAMINATED))
    # localization(type-무관)은 c2를 f2에 매칭 → tp 3 (type-strict 2보다 1 많음)
    assert s.localization.tp == 3
    assert len(s.type_confusions) == 1
    tc = s.type_confusions[0]
    assert tc.golden_type == "번복" and tc.pred_type == "모순"


def test_contaminated_false_positive_reasons():
    s = score_detection(_golden(), load_pred_flags(CONTAMINATED))
    reasons = {fp.flag_id: fp.reason for fp in s.false_positives}
    assert reasons.get("hallu1") == "ungrounded"     # 전사에 없는 인용
    assert reasons.get("spurious1") == "unmatched"   # grounding됐지만 골든에 없음
    assert reasons.get("c2") == "unmatched"          # 오타입 예측


def test_contaminated_miss_marks_type_confusion():
    s = score_detection(_golden(), load_pred_flags(CONTAMINATED))
    misses = {m.flag_id: m for m in s.misses}
    assert set(misses) == {"f2", "f3"}
    assert misses["f2"].type_confused is True        # 찾았으나 라벨 틀림
    assert misses["f3"].type_confused is False       # 순수 놓침


# ── 결정성: 같은 입력 두 번 → 같은 결과 ────────────────────────────────────

def test_scoring_is_deterministic():
    g, p = _golden(), load_pred_flags(CONTAMINATED)
    a = score_detection(g, p)
    b = score_detection(g, p)
    assert (a.overall.tp, a.overall.fp, a.overall.fn) == (b.overall.tp, b.overall.fp, b.overall.fn)
    assert [m.flag_id for m in a.misses] == [m.flag_id for m in b.misses]


# ── 리뷰 회귀 ──────────────────────────────────────────────────────────────

def _overlap_meeting():
    """세그먼트집합이 겹치는 골든 — localization이 strict의 독립 그리디였을 때
    localization TP < strict TP가 되던 [1]의 트리거."""
    tx = [TranscriptSegment("s1", "p1", "첫째 발언 내용입니다"),
          TranscriptSegment("s2", "p2", "둘째 발언 내용입니다")]
    golds = [
        FlowFlag("g0", FlagType.CONTRADICTION,
                 [Statement("p1", "첫째 발언 내용입니다"), Statement("p2", "둘째 발언 내용입니다")]),
        FlowFlag("g1", FlagType.REVERSAL, [Statement("p2", "둘째 발언 내용입니다")]),
    ]
    return {"meta": {}, "transcript": tx, "flags": golds}


def test_localization_never_below_strict():
    # [1]: localization은 strict의 확장이어야 한다 → TP·재현율이 strict보다 작을 수 없다.
    preds = [
        FlowFlag("p0", FlagType.CONTRADICTION, [Statement("p1", "첫째 발언 내용입니다")]),
        FlowFlag("p1", FlagType.REVERSAL,
                 [Statement("p2", "둘째 발언 내용입니다"), Statement("p1", "첫째 발언 내용입니다")]),
    ]
    s = score_detection(_overlap_meeting(), preds)
    assert s.overall.tp == 2
    assert s.localization.tp >= s.overall.tp
    assert s.localization.recall >= s.overall.recall


def test_strict_tp_not_reported_as_type_confusion():
    # [2]/[4]: strict에서 정타(TP)된 골든은 type_confusion에 들어가면 안 된다(이중계상).
    preds = [
        FlowFlag("p0", FlagType.CONTRADICTION, [Statement("p1", "첫째 발언 내용입니다")]),
        FlowFlag("p1", FlagType.REVERSAL,
                 [Statement("p2", "둘째 발언 내용입니다"), Statement("p1", "첫째 발언 내용입니다")]),
    ]
    s = score_detection(_overlap_meeting(), preds)
    assert s.matches and s.type_confusions == []      # 둘 다 strict 정타 → 혼동 없음


def test_score_raises_on_ungrounded_golden_flag():
    # [9]: 골든 flag 인용이 전사에 grounding 안 되면 조용한 FN 강등 대신 에러.
    tx = [TranscriptSegment("s1", "p1", "실재하는 발언")]
    bad = {"meta": {}, "transcript": tx,
           "flags": [FlowFlag("g", FlagType.UNRESOLVED, [Statement("p1", "전사에 없는 유령 인용")])]}
    with pytest.raises(ValueError):
        score_detection(bad, [])


def test_duplicate_pred_id_does_not_corrupt_fp_meta():
    # [11]: 중복 예측 id가 있어도 FP 메타(ungrounded_quotes)가 서로 오염되지 않는다.
    tx = [TranscriptSegment("s1", "p1", "실재하는 발언 하나")]
    golden = {"meta": {}, "transcript": tx,
              "flags": [FlowFlag("g", FlagType.UNRESOLVED, [Statement("p1", "실재하는 발언 하나")])]}
    preds = [
        FlowFlag("dup", FlagType.CONTRADICTION, [Statement("p1", "완전히 없는 유령 A")]),
        FlowFlag("dup", FlagType.CONTRADICTION, [Statement("p1", "실재하는 발언 하나")]),
    ]
    s = score_detection(golden, preds)
    ung = [fp for fp in s.false_positives if fp.reason == "ungrounded"]
    assert any("유령 A" in q for fp in ung for q in fp.ungrounded_quotes)

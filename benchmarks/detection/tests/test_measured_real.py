"""실측 회귀 — 실제 Claude(Opus 4.8) 감지 결과를 골든으로 채점한 값을 동결.

`fixtures/`의 mock 예측(faithful/contaminated)과 달리, `measurements/`의 pred JSON은
**실제 Anthropic API 실호출** 산출물이다(2026-07-13 KST, `claude-opus-4-8`,
`detect_bench.detect --detector claude`). LLM 출력은 비결정적이라 그대로 재현할 수 없어
**스냅샷으로 동결**한다 — 그러나 채점기(grounding/score)는 순수·결정적이므로, 동결된 pred를
골든으로 다시 채점하면 항상 같은 값이 나온다. 이 테스트는 그 값을 고정해:

  1. 문서화된 종합표(측정 결과)를 실행 가능한 형태로 박제하고,
  2. grounding.py/score.py가 나중에 바뀌어 **동일 실측 입력의 채점이 달라지면** 잡는다
     (측정 코어 회귀 가드 — 실 API 재호출 없이).

측정 요지(자세한 해석은 measurements/README.md): 3회의 15 flag 중 **정밀도 1.00**
(가짜 감지·할루시 인용·타입 혼동 0), 놓친 2건은 전부 재현율이며 **경계가 겹치는 애매한
케이스**(번복에 흡수된 자기모순 / 미래 약속형 미해결).
"""

from pathlib import Path

import pytest

from detect_bench.labels import load_meeting, load_pred_flags, validate_golden
from detect_bench.score import score_detection

_BASE = Path(__file__).resolve().parent.parent
GOLDEN = _BASE / "fixtures" / "golden"
MEASURED = _BASE / "measurements"

# 실측 채점의 동결값 — measurements/README.md 종합표와 단일 출처로 일치해야 한다.
#   segs/golden: 골든 형태  ·  tp/fp/fn: 종합(type-strict)  ·  misses: (flag_id, type, segset)
CASES = {
    "luma_meeting": dict(
        segs=25, golden=4, tp=4, fp=0, fn=0, misses=[],
    ),
    "greenmart_meeting": dict(
        segs=26, golden=6, tp=5, fp=0, fn=1,
        misses=[("f3", "모순", {"s11", "s21"})],   # 자기모순이 팀 결정 번복에 흡수됨
    ),
    "payments_postmortem": dict(
        segs=27, golden=5, tp=4, fp=0, fn=1,
        misses=[("f3", "미해결", {"s19"})],         # 미래 약속형(RCA 다음 스프린트)을 해결로 읽음
    ),
}


def _score(name):
    golden = load_meeting(GOLDEN / f"{name}.json")
    assert validate_golden(golden) is True         # 측정 대상 골든이 malformed면 채점이 무의미
    pred = load_pred_flags(MEASURED / f"{name}.pred.json")
    return golden, score_detection(golden, pred)


@pytest.mark.parametrize("name", list(CASES))
def test_measured_shape(name):
    golden, _ = _score(name)
    c = CASES[name]
    assert len(golden["transcript"]) == c["segs"]
    assert len(golden["flags"]) == c["golden"]


@pytest.mark.parametrize("name", list(CASES))
def test_measured_counts(name):
    _, s = _score(name)
    c = CASES[name]
    assert (s.overall.tp, s.overall.fp, s.overall.fn) == (c["tp"], c["fp"], c["fn"])
    # tp+fn 은 골든 flag 총수와 같아야(모든 골든은 매칭되거나 놓치거나 둘 중 하나).
    assert s.overall.tp + s.overall.fn == c["golden"]


@pytest.mark.parametrize("name", list(CASES))
def test_measured_precision_is_perfect(name):
    # 핵심 신호: 세 회의 모두 정밀도 1.00 — 가짜 감지 0, 할루시 인용 0, 타입 혼동 0.
    _, s = _score(name)
    assert s.overall.fp == 0
    assert s.overall.precision == 1.0
    assert s.false_positives == []
    assert s.type_confusions == []
    assert s.tainted_matches == []


@pytest.mark.parametrize("name", list(CASES))
def test_measured_misses(name):
    # 놓친 flag의 정체(flag_id·type·segset)를 정확히 고정 — 실패가 어디서 나는지가 측정의 값.
    _, s = _score(name)
    got = {(m.flag_id, str(m.type), frozenset(m.segments)) for m in s.misses}
    want = {(fid, typ, frozenset(segs)) for fid, typ, segs in CASES[name]["misses"]}
    assert got == want


def test_measured_aggregate():
    # 종합 실측표(3회의 합계): TP 13 / FP 0 / FN 2 → 정밀도 1.00 · 재현율 13/15.
    tp = fp = fn = 0
    for name in CASES:
        _, s = _score(name)
        tp += s.overall.tp
        fp += s.overall.fp
        fn += s.overall.fn
    assert (tp, fp, fn) == (13, 0, 2)
    assert fp == 0                                   # 정밀도 1.00 (합계에서도)
    assert tp + fn == 15                             # 골든 flag 총수(4+6+5)
    assert round(tp / (tp + fn), 3) == 0.867         # 종합 재현율

"""회의 단위 채점 + 리포트 렌더 (end-to-end 스모크)."""

from pathlib import Path

from stt_bench.golden import load_golden, load_hypothesis
from stt_bench.report import render_report, score_meeting

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def _golden():
    return load_golden(FIX / "golden" / "budget_meeting.json")


def test_contaminated_hypothesis_flags_both_errors():
    g = _golden()
    h = load_hypothesis(FIX / "hyp" / "budget_meeting.aws_mock.json")
    m = score_meeting(g, h)
    assert m["per_type"]["AMOUNT"].sub == 1   # 3천만 → 2천만
    assert m["per_type"]["DATE"].sub == 1     # 8월 → 9월
    assert len(m["false_contradiction_candidates"]) == 2


def test_clean_hypothesis_has_no_entity_errors():
    g = _golden()
    h = load_hypothesis(FIX / "hyp" / "budget_meeting.clova_mock.json")
    m = score_meeting(g, h)
    # 삼천만원(표면형만 다름)은 값 등가라 오류 아님
    assert m["per_type"]["AMOUNT"].cter == 0.0
    assert m["per_type"]["DATE"].cter == 0.0
    assert len(m["false_contradiction_candidates"]) == 0


def test_render_report_smoke():
    g = _golden()
    h = load_hypothesis(FIX / "hyp" / "budget_meeting.aws_mock.json")
    md = render_report(g, h, score_meeting(g, h))
    assert "CTER" in md
    assert "AMOUNT" in md
    assert "aws_mock" in md

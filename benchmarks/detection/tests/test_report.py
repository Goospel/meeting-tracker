"""회의 단위 리포트 + CLI."""

import json
from pathlib import Path

from detect_bench.labels import load_meeting, load_pred_flags
from detect_bench.report import format_report, main
from detect_bench.score import score_detection

FIX = Path(__file__).resolve().parent.parent / "fixtures"
GOLDEN = FIX / "golden" / "luma_meeting.json"
FAITHFUL = FIX / "pred" / "luma_meeting.faithful.json"
CONTAMINATED = FIX / "pred" / "luma_meeting.contaminated.json"


def test_report_faithful_has_no_fp_no_miss():
    g = load_meeting(GOLDEN)
    md = format_report(g, score_detection(g, load_pred_flags(FAITHFUL)))
    assert "감지 품질 리포트" in md
    assert "재현율 1.00" in md
    # 가짜/놓친 섹션은 '없음'
    assert md.count("- 없음") >= 2


def test_report_contaminated_lists_failure_modes():
    g = load_meeting(GOLDEN)
    md = format_report(g, score_detection(g, load_pred_flags(CONTAMINATED)))
    assert "할루시 인용" in md            # hallu1
    assert "타입 혼동" in md              # c2 (번복→모순)
    assert "순수 놓침" in md              # f3


def test_cli_writes_report(tmp_path):
    out = tmp_path / "report.md"
    rc = main(["--golden", str(GOLDEN), "--pred", str(CONTAMINATED), "--out", str(out)])
    assert rc == 0 and out.exists()
    body = out.read_text(encoding="utf-8")
    assert "유형별" in body and "가짜 감지" in body and "놓친 감지" in body


def test_cli_stdout_ok():
    rc = main(["--golden", str(GOLDEN), "--pred", str(FAITHFUL)])
    assert rc == 0


def test_cli_rejects_malformed_golden(tmp_path):
    data = json.loads(GOLDEN.read_text(encoding="utf-8-sig"))
    data["transcript"][0]["flags"] = ["ghost"]     # 없는 flag 역참조 → 검증 실패
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    rc = main(["--golden", str(bad), "--pred", str(FAITHFUL)])
    assert rc == 2


def test_cli_rejects_missing_key_pred(tmp_path):
    # [8]: 필수 키 누락(type 없음)은 KeyError 트레이스백이 아니라 클린 에러(return 2).
    bad = tmp_path / "bad_pred.json"
    bad.write_text(json.dumps([{"id": "x", "statements": [{"speaker": "p1", "quote": "y"}]}],
                              ensure_ascii=False), encoding="utf-8")
    rc = main(["--golden", str(GOLDEN), "--pred", str(bad)])
    assert rc == 2

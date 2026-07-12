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


def test_cli_rejects_unparseable_pred(tmp_path):
    # 구조적으로 깨진 예측 파일(리스트도 {flags:[...]}도 아님)은 트레이스백이 아니라 클린 에러(return 2).
    # (개별 flag의 id/type/quote 누락은 이제 per-flag 강등 — 배치를 죽이지 않는다.)
    bad = tmp_path / "bad_pred.json"
    bad.write_text(json.dumps({"predictions": []}, ensure_ascii=False), encoding="utf-8")  # "flags" 키 없음
    rc = main(["--golden", str(GOLDEN), "--pred", str(bad)])
    assert rc == 2


# ── 리뷰2 회귀: FP 표기 일관성 · 정타 속 할루시 섹션 ───────────────────────

def test_report_distinguishes_type_confused_fp():
    # [리뷰2]: 타입혼동 FP(c2)가 '골든에 대응 없음'으로 표기되면 같은 리포트의
    # 🔵 타입 혼동 섹션(f2↔c2 대응 명시)과 자기모순 → 라벨 오분류로 안내해야 한다.
    from detect_bench.labels import FlagType, FlowFlag, Statement, TranscriptSegment  # noqa: F401
    g = load_meeting(GOLDEN)
    md = format_report(g, score_detection(g, load_pred_flags(CONTAMINATED)))
    c2_line = next(l for l in md.splitlines() if l.startswith("- `c2`"))
    assert "골든에 대응 없음" not in c2_line
    assert "라벨" in c2_line


def test_report_lists_tainted_match_quotes():
    # [리뷰2]: 정타(TP) 예측 속 할루시 인용이 리포트에 드러나야 한다.
    from detect_bench.labels import FlagType, FlowFlag, Statement, TranscriptSegment
    tx = [TranscriptSegment("s1", "p1", "실제 첫째 발언입니다"),
          TranscriptSegment("s2", "p2", "실제 둘째 발언입니다")]
    g = {"meta": {"title": "t"}, "transcript": tx,
         "flags": [FlowFlag("g", FlagType.CONTRADICTION,
                            [Statement("p1", "실제 첫째 발언입니다"),
                             Statement("p2", "실제 둘째 발언입니다")])]}
    pred = FlowFlag("p", FlagType.CONTRADICTION,
                    [Statement("p1", "실제 첫째 발언입니다"),
                     Statement("p9", "완전 조작 유령 인용")])
    md = format_report(g, score_detection(g, [pred]))
    assert "완전 조작 유령 인용" in md


def test_report_no_evidence_fp_not_labeled_hallucination():
    # [리뷰2]: statements 빈 예측은 '할루시 인용'이 아니라 '근거 인용 없음'으로.
    from detect_bench.labels import FlagType, FlowFlag, Statement, TranscriptSegment
    tx = [TranscriptSegment("s1", "p1", "실재하는 발언 하나")]
    g = {"meta": {}, "transcript": tx,
         "flags": [FlowFlag("g", FlagType.UNRESOLVED, [Statement("p1", "실재하는 발언 하나")])]}
    md = format_report(g, score_detection(g, [FlowFlag("empty", FlagType.CONTRADICTION, [])]))
    empty_line = next(l for l in md.splitlines() if l.startswith("- `empty`"))
    assert "할루시" not in empty_line
    assert "근거 인용 없음" in empty_line


def test_report_escapes_pipe_in_unknown_type():
    # [리뷰 sweep #1]: 강등된 미지 예측 type에 파이프(|)가 있으면 마크다운 표 열이 깨진다 → 이스케이프.
    from detect_bench.labels import FlagType, FlowFlag, Statement, TranscriptSegment
    tx = [TranscriptSegment("s1", "p1", "실재하는 발언 하나")]
    g = {"meta": {}, "transcript": tx,
         "flags": [FlowFlag("g", FlagType.UNRESOLVED, [Statement("p1", "실재하는 발언 하나")])]}
    pred = FlowFlag("bad", "A|B|C", [Statement("p1", "실재하는 발언 하나")])   # 파이프 든 미지 type
    md = format_report(g, score_detection(g, [pred]))
    # 표의 모든 데이터 행은 헤더와 같은 열 수(파이프 개수)를 가져야 한다.
    rows = [l for l in md.splitlines() if l.startswith("|") and "---" not in l]
    header_pipes = rows[0].count("|")
    assert all(r.count("|") == header_pipes for r in rows), "미지 type의 파이프가 표를 깨뜨림"


def test_report_neutralizes_cr_in_unknown_type():
    # [리뷰2 #4]: 미지 예측 type의 캐리지리턴(\r)도 CommonMark에서 줄바꿈이라 표 행을 쪼갠다 → 무력화.
    from detect_bench.labels import FlagType, FlowFlag, Statement, TranscriptSegment
    tx = [TranscriptSegment("s1", "p1", "실재하는 발언 하나")]
    g = {"meta": {}, "transcript": tx,
         "flags": [FlowFlag("g", FlagType.UNRESOLVED, [Statement("p1", "실재하는 발언 하나")])]}
    pred = FlowFlag("bad", "모순\r번복", [Statement("p1", "실재하는 발언 하나")])
    md = format_report(g, score_detection(g, [pred]))
    assert "\r" not in md


def test_report_neutralizes_html_in_unknown_type():
    # [리뷰4 K9] 미지 예측 type의 raw HTML(<img onerror=...>)이 _safe를 통과하면 마크다운
    # 뷰어에서 인라인 HTML로 렌더된다 — 엔티티로 무력화.
    from detect_bench.labels import FlagType, FlowFlag, Statement, TranscriptSegment
    tx = [TranscriptSegment("s1", "p1", "실재하는 발언 하나")]
    g = {"meta": {}, "transcript": tx,
         "flags": [FlowFlag("g", FlagType.UNRESOLVED, [Statement("p1", "실재하는 발언 하나")])]}
    pred = FlowFlag("bad", '<img onerror="x">', [Statement("p1", "실재하는 발언 하나")])
    md = format_report(g, score_detection(g, [pred]))
    assert "<img" not in md
    assert "&lt;img" in md


def test_cli_rejects_null_text_golden(tmp_path):
    # [리뷰4 G10 e2e] 골든 text:null은 트레이스백(exit 1)이 아니라 클린 에러 rc=2.
    bad = tmp_path / "bad_golden.json"
    bad.write_text(json.dumps(
        {"transcript": [{"id": "s1", "speaker": "p1", "text": None}], "flags": []},
        ensure_ascii=False), encoding="utf-8")
    pred = tmp_path / "pred.json"
    pred.write_text("[]", encoding="utf-8")
    rc = main(["--golden", str(bad), "--pred", str(pred)])
    assert rc == 2

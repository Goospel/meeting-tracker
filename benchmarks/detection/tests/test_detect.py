"""감지 어댑터 레이어 — 골든 전사 → 프롬프트 → 감지 포트 → 응답 파싱 → pred flags.

stage-2 채점기의 *앞단*. 지금까지는 mock pred JSON을 채점기에 직접 먹였지만, 실제로는
전사본을 Claude에 넣어 flag JSON을 받아야 한다. 이 레이어는:
  - 프롬프트 빌더(정답 누출 없이 전사만 제시),
  - 응답 파서(Claude 자유형식 출력에서 flags JSON을 견고하게 추출),
  - 감지 포트(리플레이=크레덴셜 불요 실동작 / Claude=크레덴셜 게이트, stdlib HTTP),
를 제공한다. Track A 렌더 레이어와 같은 패턴(Port + 크레덴셜-불요 실검증 + 게이트 확장점).

순수 코어(프롬프트·파서)는 크레덴셜 0으로 전량 검증되고, 실제 API는 게이트 뒤.
"""

import json
from pathlib import Path

import pytest

from detect_bench.detect import (
    ClaudeDetectorPort,
    DetectorCredentialError,
    ReplayDetectorPort,
    build_detection_prompt,
    get_detector,
    parse_detection_response,
    run_detection,
)
from detect_bench.labels import (
    FlagType,
    load_meeting,
    load_pred_flags,
    pred_flags_from_items,
    validate_golden,
)
from detect_bench.score import score_detection

FIX = Path(__file__).resolve().parent.parent / "fixtures"
GOLDEN = FIX / "golden" / "luma_meeting.json"


def _meeting():
    return load_meeting(GOLDEN)


# 골든 전사 세그먼트의 verbatim 부분문자열 인용 — 4종 전부 grounding되는 '충실한' Claude 출력.
_FAITHFUL_JSON = {
    "flags": [
        {"id": "d1", "type": "모순", "statements": [
            {"speaker": "p2", "quote": "3천만원까지는 무리 없이 쓸 수 있어요"},
            {"speaker": "p2", "quote": "예산은 2천만원이 상한이라"},
        ]},
        {"id": "d2", "type": "번복", "statements": [
            {"speaker": "p1", "quote": "8월 셋째 주 출시로 다들 맞춰가는 걸로 하죠"},
            {"speaker": "p4", "quote": "9월 초에 출시하는 거니까"},
        ]},
        {"id": "d3", "type": "미해결", "statements": [
            {"speaker": "p1", "quote": "사전예약 이벤트 한 번 돌리면"},
        ]},
        {"id": "d4", "type": "재논의", "statements": [
            {"speaker": "p2", "quote": "자동요약은 회의록 수준으로 길게 뽑는 게 맞지 않을까요"},
            {"speaker": "p3", "quote": "3줄 요약처럼 짧아야 쓴다고 보거든요"},
        ]},
    ]
}


def _faithful_response() -> str:
    """실제 Claude 출력 흉내 — 한국어 서문 + ```json 펜스 + 후문."""
    return (
        "회의 전사를 분석했습니다. 흐름단절 4건을 발견했습니다:\n\n"
        "```json\n" + json.dumps(_FAITHFUL_JSON, ensure_ascii=False, indent=2) + "\n```\n\n"
        "각 인용은 전사본에서 그대로 가져왔습니다."
    )


# ── 프롬프트 빌더 ──────────────────────────────────────────────────────────

def test_prompt_includes_every_transcript_segment_text():
    p = build_detection_prompt(_meeting())
    for seg in _meeting()["transcript"]:
        assert seg.text in p, f"세그먼트 {seg.segment_id} 텍스트가 프롬프트에 없음"


def test_prompt_lists_the_four_flag_types():
    p = build_detection_prompt(_meeting())
    for t in ("모순", "번복", "미해결", "재논의"):
        assert t in p


def test_prompt_specifies_json_output_contract():
    p = build_detection_prompt(_meeting())
    # 채점기가 소비하는 키를 명시해야 grounding·매칭이 성립한다.
    assert "flags" in p and "quote" in p and "speaker" in p


def test_prompt_does_not_leak_golden_flag_ids():
    # 세그먼트 역참조(seg.flags=["f1"..])가 프롬프트에 새면 정답을 흘리는 것.
    p = build_detection_prompt(_meeting())
    for fid in ("f1", "f2", "f3", "f4"):
        assert fid not in p


def test_prompt_does_not_leak_golden_labels_or_summary():
    # 골든 flag 제목/설명·회의 summary(결정 요약)는 정답이므로 절대 프롬프트에 넣지 않는다.
    p = build_detection_prompt(_meeting())
    assert "예산 상한 발언이 뒤바뀜" not in p          # f1 title
    assert "실제 확정된 사항이 흐려짐" not in p         # summary.headline
    assert "회의 중 두 번 뒤바뀜" not in p              # summary.decisions


# ── 응답 파서 ──────────────────────────────────────────────────────────────

def test_parse_fenced_json():
    items = parse_detection_response(_faithful_response())
    assert [f["id"] for f in items] == ["d1", "d2", "d3", "d4"]


def test_parse_bare_json_object():
    items = parse_detection_response(json.dumps(_FAITHFUL_JSON, ensure_ascii=False))
    assert len(items) == 4


def test_parse_bare_json_array():
    items = parse_detection_response(json.dumps(_FAITHFUL_JSON["flags"], ensure_ascii=False))
    assert [f["id"] for f in items] == ["d1", "d2", "d3", "d4"]


def test_parse_prose_before_and_after_bare_object():
    raw = "분석 결과는 다음과 같습니다.\n" + json.dumps(_FAITHFUL_JSON, ensure_ascii=False) + "\n감사합니다."
    assert len(parse_detection_response(raw)) == 4


def test_parse_empty_flags_is_valid_zero_detection():
    # Claude가 흐름단절을 하나도 못 찾음 = 유효한 결과([]). 추출 실패(raise)와 구분돼야 한다.
    assert parse_detection_response('{"flags": []}') == []


def test_parse_unextractable_raises_not_silent_empty():
    # JSON이 전혀 없는 순수 산문 → 클린 에러. '0건 감지'로 무성 통과하면 벤치가 오염된다.
    with pytest.raises(ValueError):
        parse_detection_response("죄송하지만 이 회의에서는 특별한 문제를 찾지 못했습니다.")


def test_parse_skips_stray_non_pred_object_and_finds_flags():
    # 서문에 예시 오브젝트({speaker..})가 있어도 flags 컨테이너를 찾아낸다.
    raw = ('예를 들면 {"speaker": "p1"} 같은 형식입니다.\n'
           + json.dumps(_FAITHFUL_JSON, ensure_ascii=False))
    assert len(parse_detection_response(raw)) == 4


def test_parse_non_string_raises():
    with pytest.raises(ValueError):
        parse_detection_response(None)


# ── 리뷰 회귀: 파서가 컨테이너를 '첫 [' 가 아니라 'flags 키'로 골라야 함 (HIGH) ──

def test_parse_empty_array_before_flags_is_not_silent_zero():
    # 진짜 flags 앞에 빈 배열이 있어도 '0건 감지'로 무성 통과하면 안 된다(벤치 오염).
    raw = "해당 없음: []\n\n" + json.dumps(_FAITHFUL_JSON, ensure_ascii=False)
    assert len(parse_detection_response(raw)) == 4


def test_parse_stray_number_array_before_flags_is_ignored():
    # 산문 속 유효 JSON 배열([1,2])이 진짜 flags를 가로채면 안 된다.
    raw = "우선순위 [1, 2] 순.\n" + json.dumps(_FAITHFUL_JSON, ensure_ascii=False)
    assert [f["id"] for f in parse_detection_response(raw)] == ["d1", "d2", "d3", "d4"]


def test_parse_stray_non_flag_dict_array_before_flags_is_ignored():
    # flag이 아닌 dict 배열([{speaker,quote}])이 flags를 강탈해 엉터리 flag를 만들면 안 된다.
    raw = ('예시: [{"speaker": "p1", "quote": "x"}]\n'
           + json.dumps(_FAITHFUL_JSON, ensure_ascii=False))
    assert [f["id"] for f in parse_detection_response(raw)] == ["d1", "d2", "d3", "d4"]


def test_parse_flags_key_wins_over_earlier_flaglike_array():
    # flag스러운 stray 배열이 앞서도 명시적 {"flags":...} 컨테이너가 우선.
    raw = ('요약 [{"type": "모순"}]\n' + json.dumps(_FAITHFUL_JSON, ensure_ascii=False))
    assert len(parse_detection_response(raw)) == 4


def test_parse_unwrapped_single_flag_object_recovered():
    # 래퍼 없는 단일 flag 오브젝트 → 내부 statements 배열을 flags로 오인하지 말고 그 flag을 살린다.
    raw = ('{"id": "d1", "type": "모순", "statements": '
           '[{"speaker": "p2", "quote": "3천만원까지는 무리 없이 쓸 수 있어요"}]}')
    items = parse_detection_response(raw)
    assert [f["id"] for f in items] == ["d1"]


# ── 리뷰 2R 회귀: 래퍼 에코 강탈 + bare 배열 부분손실 (HIGH/MED) ──

def test_parse_empty_flags_wrapper_echo_before_real_is_not_silent_zero():
    # 서두에 형식 에코 {"flags": []}가 있어도 진짜 4건을 놓치면 안 된다(빈 컨테이너 강탈).
    raw = ('출력 형식은 {"flags": []} 입니다.\n실제 결과:\n'
           + json.dumps(_FAITHFUL_JSON, ensure_ascii=False))
    assert len(parse_detection_response(raw)) == 4


def test_parse_example_wrapper_echo_before_real_is_ignored():
    # 서두에 예시 컨테이너가 있어도 진짜(원소 더 많은) 컨테이너를 채택.
    raw = ('예시: {"flags": [{"id": "예시", "type": "모순", "statements": []}]}\n'
           + json.dumps(_FAITHFUL_JSON, ensure_ascii=False))
    assert [f["id"] for f in parse_detection_response(raw)] == ["d1", "d2", "d3", "d4"]


def test_parse_trailing_empty_wrapper_echo_after_real_is_ignored():
    # 진짜 답 뒤에 형식 에코 {"flags": []}가 붙어도(마지막 위치) 진짜를 택한다 — '첫/마지막'이
    # 아니라 '실제 flag 수 최대'가 기준.
    raw = (json.dumps(_FAITHFUL_JSON, ensure_ascii=False)
           + '\n참고로 흐름단절이 없으면 {"flags": []} 로 냅니다.')
    assert len(parse_detection_response(raw)) == 4


def test_parse_bare_array_with_one_degraded_element_keeps_all():
    # bare 배열의 한 원소가 flag스럽지 않아도 배열 전체를 강등 경로로 — 단일 flag로 축소해
    # 나머지를 무성 폐기하면 안 된다(부분손실).
    raw = ('[{"id": "1", "type": "모순", "statements": [{"speaker": "p2", "quote": "a"}]}, '
           '{"id": "2"}, '
           '{"id": "3", "type": "번복", "statements": [{"speaker": "p1", "quote": "b"}]}]')
    items = parse_detection_response(raw)
    assert [i.get("id") for i in items] == ["1", "2", "3"]
    flags = pred_flags_from_items(items)               # 원소 2는 강등되되 유실 안 됨
    assert [f.flag_id for f in flags] == ["1", "2", "3"]


# ── 리뷰 회귀: 실 Claude 포트의 클린 에러 계약 (MED) ──

def test_api_response_non_string_text_block_raises_clean_not_typeerror():
    def bad(url, headers, body):
        return json.dumps({"content": [{"type": "text", "text": None}]}).encode("utf-8")

    port = ClaudeDetectorPort(api_key="sk-test", transport=bad)
    with pytest.raises(ValueError):        # TypeError(트레이스백)가 아니라 클린 ValueError
        port.detect("프롬프트")


def test_urllib_post_httperror_becomes_clean_value_error(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    from detect_bench.detect import _urllib_post

    def boom(req):
        raise urllib.error.HTTPError(
            req.full_url, 429, "Too Many Requests", {},
            io.BytesIO(b'{"error":{"message":"rate limited"}}'))

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(ValueError) as ei:
        _urllib_post("https://api.anthropic.com/v1/messages", {}, b"{}")
    assert "429" in str(ei.value)          # 유용한 API 에러 정보가 소실되지 않음


# ── 리뷰 회귀: 프롬프트 정답 좌표 누출 + 고아 픽스처 (MED/LOW) ──

def test_prompt_example_does_not_prime_golden_answer_coordinates():
    # 예시가 골든 f1의 좌표(p2 @2510)를 앵커로 주면 감지 벤치가 낙관 편향된다.
    # 2510은 전사 세그먼트 s23의 시각으로 딱 1번만 나와야 한다(예시가 이를 복제하면 2번).
    p = build_detection_prompt(_meeting())
    assert p.count("2510") == 1


def test_replay_fixture_file_grounds_and_scores_perfect():
    # 고아 방지 — 리플레이 픽스처를 실제로 소비해 골든 변경 시 drift를 잡는다.
    resp = (FIX / "response" / "luma_meeting.claude.txt").read_text(encoding="utf-8")
    meeting = _meeting()
    pred = run_detection(meeting, ReplayDetectorPort(resp))
    score = score_detection(meeting, pred)
    assert score.overall.tp == 4 and score.misses == []


def test_parse_result_feeds_pred_loader_with_degradation():
    # 파서 산출물은 load_pred_flags와 같은 강등 경로를 타야 한다(변형 flag가 배치를 안 죽임).
    raw = '{"flags": [{"id": "ok", "type": "모순", "statements": []}, "쓰레기", {"id": "x"}]}'
    items = parse_detection_response(raw)
    flags = pred_flags_from_items(items)              # 비-dict "쓰레기"는 건너뜀
    assert [f.flag_id for f in flags] == ["ok", "x"]


# ── 리플레이 포트 (크레덴셜 불요 실동작) ────────────────────────────────────

def test_replay_port_returns_canned_response_ignoring_prompt():
    port = ReplayDetectorPort(_faithful_response())
    assert port.detect("아무 프롬프트나") == _faithful_response()


def test_replay_end_to_end_scores_perfect_recall():
    # 프롬프트 → 리플레이 → 파싱 → pred flags → 채점. 전 파이프라인을 크레덴셜 0으로 관통.
    meeting = _meeting()
    validate_golden(meeting)
    pred = run_detection(meeting, ReplayDetectorPort(_faithful_response()))
    score = score_detection(meeting, pred)
    assert score.overall.recall == 1.0                # 4종 전부 매칭
    assert score.misses == []
    assert score.overall.tp == 4


def test_get_detector_replay_builds_working_port():
    port = get_detector("replay", response=_faithful_response())
    assert isinstance(port, ReplayDetectorPort)
    assert len(parse_detection_response(port.detect("x"))) == 4


# ── Claude 포트 (크레덴셜 게이트, stdlib HTTP) ──────────────────────────────

def test_claude_port_without_key_raises_credential_error():
    port = ClaudeDetectorPort(api_key=None)
    with pytest.raises(DetectorCredentialError):
        port.detect("프롬프트")


def test_get_detector_claude_without_key_raises_credential_error():
    with pytest.raises(DetectorCredentialError):
        get_detector("claude", api_key=None)


def test_get_detector_unknown_name_raises():
    with pytest.raises(ValueError):
        get_detector("bogus")


def test_claude_port_with_injected_transport_builds_request_and_parses():
    captured = {}

    def fake_transport(url, headers, body):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(body.decode("utf-8"))
        return json.dumps(
            {"content": [{"type": "text", "text": _faithful_response()}]}
        ).encode("utf-8")

    port = ClaudeDetectorPort(api_key="sk-test", model="claude-opus-4-8",
                              transport=fake_transport)
    out = port.detect("PROMPT-SENTINEL")

    assert out == _faithful_response()
    assert captured["url"].endswith("/v1/messages")
    assert captured["headers"]["x-api-key"] == "sk-test"
    assert "anthropic-version" in captured["headers"]
    assert captured["body"]["model"] == "claude-opus-4-8"
    # 프롬프트가 요청 본문에 실려 나가야 한다.
    sent = json.dumps(captured["body"], ensure_ascii=False)
    assert "PROMPT-SENTINEL" in sent


def test_claude_port_malformed_api_response_raises_clean():
    def bad_transport(url, headers, body):
        return json.dumps({"error": {"message": "quota"}}).encode("utf-8")

    port = ClaudeDetectorPort(api_key="sk-test", transport=bad_transport)
    with pytest.raises(ValueError):
        port.detect("프롬프트")


# ── CLI ────────────────────────────────────────────────────────────────────

def _run_cli(argv):
    from detect_bench.detect import main
    return main(argv)


def test_cli_replay_writes_pred_json_that_scores(tmp_path, capsys):
    resp = tmp_path / "resp.txt"
    resp.write_text(_faithful_response(), encoding="utf-8")
    out = tmp_path / "pred.json"
    rc = _run_cli(["--golden", str(GOLDEN), "--detector", "replay",
                   "--response", str(resp), "--out", str(out)])
    assert rc == 0
    # 산출된 pred JSON을 기존 로더가 소비하고 채점기가 만점 매칭.
    pred = load_pred_flags(out)
    score = score_detection(_meeting(), pred)
    assert score.overall.tp == 4


def test_cli_claude_without_key_clean_error(capsys):
    rc = _run_cli(["--golden", str(GOLDEN), "--detector", "claude"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err


def test_cli_unextractable_response_clean_error(tmp_path, capsys):
    resp = tmp_path / "prose.txt"
    resp.write_text("문제를 찾지 못했습니다. JSON 없음.", encoding="utf-8")
    rc = _run_cli(["--golden", str(GOLDEN), "--detector", "replay",
                   "--response", str(resp), "--out", str(tmp_path / "x.json")])
    assert rc == 2
    assert "Traceback" not in capsys.readouterr().err

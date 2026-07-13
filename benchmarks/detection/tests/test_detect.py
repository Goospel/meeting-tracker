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
    meeting = _meeting()                               # 골든 1회 로드 재사용(중복 디스크 파싱 제거)
    p = build_detection_prompt(meeting)
    for seg in meeting["transcript"]:
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

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 429, "Too Many Requests", {},
            io.BytesIO(b'{"error":{"message":"rate limited"}}'))

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(ValueError) as ei:
        _urllib_post("https://api.anthropic.com/v1/messages", {}, b"{}")
    assert "429" in str(ei.value)          # 유용한 API 에러 정보가 소실되지 않음


# ── 리뷰 회귀: 프롬프트 정답 좌표 누출 + 고아 픽스처 (MED/LOW) ──

def test_prompt_example_does_not_prime_golden_answer_coordinates():
    # 예시가 골든 f1의 좌표(p2 @1240·@2510)를 앵커로 주면 감지 벤치가 낙관 편향된다.
    # 각 시각은 전사 세그먼트의 시각으로 딱 1번만 나와야 한다(예시가 복제하면 2번).
    # [3R] 2510만 가드하면 예시가 1240을 되노출해도 통과한다 — 두 좌표 모두 가드.
    p = build_detection_prompt(_meeting())
    assert p.count("2510") == 1
    assert p.count("1240") == 1


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
    # [3R] 게이트는 생성 시점(__init__) 단일 지점 — detect()까지 가서야 터지면 팩토리와
    # 직접 생성 경로가 각자 게이트를 복제해야 한다(메시지 드리프트 실재).
    with pytest.raises(DetectorCredentialError):
        ClaudeDetectorPort(api_key=None)


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


def test_cli_claude_without_key_clean_error(capsys, monkeypatch):
    # [3R] 환경변수를 지우고 시작 — 지우지 않으면 키가 설정된 개발 환경에서 이 단위테스트가
    # 골든 전사 전체를 실제 Anthropic API로 POST한다(과금·행·rc=0 위양성).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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


# ── 리뷰 3R 회귀: 파서 극단 카디널리티(0건·1건·절단) + 프롬프트 에코 시딩 뿌리 제거 ──
# 3R(xhigh) 핵심: 휴리스틱은 '진짜 답 4건' 케이스에선 강했지만 0건·1건·절단에서 뒤집혔다.
# 뿌리 수정 = 프롬프트 예시를 JSON 문법 밖 표기로(에코가 파싱 후보 자체가 못 되게).

# 프롬프트의 새(비파싱) 예시를 모델이 서두/후미에 그대로 에코했다고 가정한 문자열.
_UNPARSEABLE_EXAMPLE_ECHO = (
    '{"flags": [{"id": "1", "type": "모순", "statements": ['
    '{"speaker": "p1", "quote": "(전사에서 그대로 복사한 앞 발언)", "time_sec": <그 발언의 초>}'
    ']}]}'
)

_ONE_FLAG = {"id": "r1", "type": "모순", "statements": [
    {"speaker": "p2", "quote": "3천만원까지는 무리 없이 쓸 수 있어요"},
    {"speaker": "p2", "quote": "예산은 2천만원이 상한이라"},
]}


def test_prompt_itself_yields_no_extractable_flags():
    # [3R 뿌리] 프롬프트가 파싱 가능한 예시/0건 리터럴을 담으면 그 에코가 파서의 후보가 된다 —
    # 프롬프트 원문에서 flags가 추출되면 안 된다(추출 시 예시가 에코 강탈 후보라는 뜻).
    with pytest.raises(ValueError):
        parse_detection_response(build_detection_prompt(_meeting()))


def test_parse_zero_detection_survives_example_echo():
    # [3R P1①] 예시 에코 + 진짜 답 {"flags": []}(정당한 0건) → 0건이 살아남아야 한다.
    # 구(파싱 가능) 예시였다면 에코 n=1 > 진짜 n=0으로 더미가 채택돼 가짜 FP 1건이 됐다.
    raw = ("예시 형식 재확인: " + _UNPARSEABLE_EXAMPLE_ECHO
           + '\n실제 결과: {"flags": []}')
    assert parse_detection_response(raw) == []


def test_parse_single_flag_answer_survives_trailing_example_echo():
    # [3R P1②] 진짜 1-flag 답 뒤에 예시 에코 — 동수 타이브레이크로 에코가 이기면 안 된다.
    raw = (json.dumps({"flags": [_ONE_FLAG]}, ensure_ascii=False)
           + "\n참고로 예시 형식은 " + _UNPARSEABLE_EXAMPLE_ECHO + " 입니다.")
    assert [f["id"] for f in parse_detection_response(raw)] == ["r1"]


def test_parse_bare_array_answer_beats_trailing_empty_wrapper_echo():
    # [3R P3] 진짜 답이 bare 배열(공식 지원 형태) + 후행 규칙 에코 {"flags": []} —
    # 빈 컨테이너의 절대 우선권이 내용 있는 flag스러운 후보를 강탈하면 안 된다.
    raw = (json.dumps(_FAITHFUL_JSON["flags"], ensure_ascii=False)
           + '\n흐름단절이 없으면 {"flags": []} 를 출력합니다.')
    assert [f["id"] for f in parse_detection_response(raw)] == ["d1", "d2", "d3", "d4"]


def test_parse_statement_dict_echo_container_does_not_beat_real_flags():
    # [3R P4] flag 수 카운트는 '임의 dict'가 아니라 flag스러운 dict만 세야 한다 —
    # statement dict 3개짜리 기형 에코가 진짜 2-flag 답을 수로 누르면 안 된다.
    echo = ('{"flags": [{"speaker": "p1", "quote": "x"}, '
            '{"speaker": "p2", "quote": "y"}, {"speaker": "p3", "quote": "z"}]}')
    real = json.dumps({"flags": [
        _ONE_FLAG,
        {"id": "r2", "type": "번복", "statements": [{"speaker": "p1", "quote": "b"}]},
    ]}, ensure_ascii=False)
    assert [f["id"] for f in parse_detection_response(echo + "\n" + real)] == ["r1", "r2"]


def test_parse_truncated_wrapper_raises_clean_not_partial_salvage():
    # [3R P2ⓑ] max_tokens 절단 등으로 {"flags": [...} 래퍼가 미완성이면, 완성된 내부 flag
    # 조각을 주워 '부분 결과'로 무성 통과하면 안 된다(10건 감지→1건 둔갑) — 클린 에러.
    full = json.dumps(_FAITHFUL_JSON, ensure_ascii=False)
    truncated = full[: full.index('"d3"')]              # 3번째 flag 중간에서 절단
    with pytest.raises(ValueError):
        parse_detection_response(truncated)


def test_parse_fallback_prefers_larger_flaglike_candidate():
    # [3R P2ⓐ] flags 래퍼가 전무할 때 차선은 '첫 후보'가 아니라 flag스러운 원소가 가장 많은
    # 후보 — 서두의 flag스러운 에코 오브젝트가 뒤의 진짜 bare 배열을 강탈하면 안 된다.
    raw = ('예: {"id": "0", "type": "모순", "statements": []}\n'
           + json.dumps(_FAITHFUL_JSON["flags"], ensure_ascii=False))
    assert [f["id"] for f in parse_detection_response(raw)] == ["d1", "d2", "d3", "d4"]


def test_parse_degenerate_nesting_raises_clean_not_recursionerror():
    # [3R P5] '['*3000 같은 퇴화 입력의 RecursionError가 파서·CLI except절을 뚫으면 안 된다.
    with pytest.raises(ValueError):
        parse_detection_response("[" * 3000)


def test_parse_recovers_flags_after_degenerate_nesting():
    # 퇴화 구간이 앞에 있어도 그 뒤의 유효한 flags 컨테이너에 도달해야 한다.
    raw = "[" * 3000 + "\n" + json.dumps(_FAITHFUL_JSON, ensure_ascii=False)
    assert len(parse_detection_response(raw)) == 4


def test_parse_all_nondict_flags_container_raises():
    # [3R] '전량 비-dict flags' 가드는 CLI의 버려지는 조기 검증이 아니라 파서 자신의 계약 —
    # 어댑터 경유 모든 호출자가 같은 가드를 얻는다.
    with pytest.raises(ValueError):
        parse_detection_response('{"flags": ["1. 모순: ...", "2. 번복: ..."]}')


def test_api_response_max_tokens_truncation_raises_clean():
    # [3R P6] stop_reason=max_tokens면 텍스트가 정상처럼 보여도 절단이다 — 파서로 흘려보내
    # 부분 결과가 되기 전에 포트에서 클린 에러로 잡는다.
    def truncating(url, headers, body):
        return json.dumps({
            "stop_reason": "max_tokens",
            "content": [{"type": "text", "text": '{"flags": [{"id": "1", "type"'}],
        }).encode("utf-8")

    port = ClaudeDetectorPort(api_key="sk-test", transport=truncating)
    with pytest.raises(ValueError) as ei:
        port.detect("프롬프트")
    assert "max_tokens" in str(ei.value)


def test_api_error_message_nonstring_still_clean_valueerror():
    # [3R P8] 비표준 게이트웨이가 error.message를 dict로 줘도 TypeError(트레이스백)가 아니라
    # 클린 ValueError — 클린 에러 처리를 위해 존재하는 함수가 그 경로에서 죽으면 안 된다.
    def bad(url, headers, body):
        return json.dumps({"error": {"message": {"detail": "quota"}}}).encode("utf-8")

    port = ClaudeDetectorPort(api_key="sk-test", transport=bad)
    with pytest.raises(ValueError):
        port.detect("프롬프트")


def test_urllib_post_passes_finite_timeout(monkeypatch):
    # [3R P9] timeout 없는 urlopen은 소켓 기본이 무한이라 네트워크 스톨 시 CLI가 영구 행 —
    # 유한 timeout이 전달돼야 한다(socket.timeout은 OSError라 기존 클린 에러 경로에 잡힘).
    import urllib.request

    from detect_bench.detect import _urllib_post

    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    _urllib_post("https://api.anthropic.com/v1/messages", {}, b"{}")
    assert captured["timeout"] is not None and captured["timeout"] > 0


def test_claude_port_whitespace_key_raises_credential_error():
    # [3R P10] 공백만 든 키(.env 실수)는 truthy라 falsy 게이트를 뚫고 실 네트워크 401까지
    # 간다 — 게이트는 strip 후 판정해야 안내 메시지(--detector replay 힌트)가 유지된다.
    with pytest.raises(DetectorCredentialError):
        ClaudeDetectorPort(api_key="   ")
    with pytest.raises(DetectorCredentialError):
        get_detector("claude", api_key=" ")


def test_get_detector_forwards_max_tokens():
    # [3R P7] 팩토리가 max_tokens를 포워딩하지 않으면 절단(P6) 완화 수단이 지원 경로에 없다.
    port = get_detector("claude", api_key="sk-test", max_tokens=8192)
    assert port._max_tokens == 8192


def test_cli_accepts_max_tokens_flag(capsys, monkeypatch):
    # [3R P7] CLI에도 상한 조정 노브가 있어야 한다 — 키 없이도 플래그 파싱 자체는 성립(rc=2).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = _run_cli(["--golden", str(GOLDEN), "--detector", "claude", "--max-tokens", "8192"])
    assert rc == 2                                     # 크레덴셜 게이트(플래그는 정상 수용)
    assert "Traceback" not in capsys.readouterr().err


def test_cli_out_write_failure_is_clean_error(tmp_path, capsys):
    # [3R P11] 출력 쓰기 실패(OSError)도 로드/감지 실패와 같은 클린 에러(rc=2) — 쓰기 블록만
    # try 밖이면 감지까지 성공한 뒤 트레이스백으로 죽는 비대칭이 생긴다.
    resp = tmp_path / "resp.txt"
    resp.write_text(_faithful_response(), encoding="utf-8")
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("파일", encoding="utf-8")
    rc = _run_cli(["--golden", str(GOLDEN), "--detector", "replay",
                   "--response", str(resp), "--out", str(blocker / "pred.json")])
    assert rc == 2
    assert "Traceback" not in capsys.readouterr().err


def test_cli_scrubs_nonfinite_to_null_in_pred_file(tmp_path, capsys):
    # [3R P12] 응답 속 NaN/Infinity가 pred 파일에 그대로 직렬화되면 RFC 8259 위반 산출물 —
    # 신뢰 불가 좌표는 null로 강등(per-flag 강등 철학)하고 파일은 표준 JSON을 유지한다.
    resp = tmp_path / "resp.txt"
    resp.write_text(
        '{"flags": [{"id": "1", "type": "모순", "statements": '
        '[{"speaker": "p1", "quote": "x", "time_sec": NaN}]}]}',
        encoding="utf-8")
    out = tmp_path / "pred.json"
    rc = _run_cli(["--golden", str(GOLDEN), "--detector", "replay",
                   "--response", str(resp), "--out", str(out)])
    assert rc == 0
    text = out.read_text(encoding="utf-8")

    def _no_const(name):                               # 표준 JSON 검증 — NaN/Infinity 리터럴 금지
        raise AssertionError(f"비표준 JSON 상수: {name}")

    data = json.loads(text, parse_constant=_no_const)
    assert data["flags"][0]["statements"][0]["time_sec"] is None


def test_prompt_title_null_renders_default_not_none():
    # [3R P17] 명시적 null title은 dict.get 기본값을 우회한다 — 'None'이 프롬프트에 새면 안 됨.
    meeting = {"meta": {"title": None, "participants": [
        {"id": None, "name": None, "role": "PM"}]}, "transcript": []}
    p = build_detection_prompt(meeting)
    assert "(제목 없음)" in p
    assert "None" not in p


def test_prompt_participant_partial_fields_render_clean():
    # [3R SW3] name/role 중 하나만 있는 참석자가 '- p1 (, 영업)' 기형으로 렌더되면 안 된다.
    meeting = {"meta": {"title": "회의", "participants": [
        {"id": "p1", "role": "영업"},
        {"id": "p2", "name": "박팀장"},
    ]}, "transcript": []}
    p = build_detection_prompt(meeting)
    assert "- p1 (영업)" in p
    assert "- p2 (박팀장)" in p
    assert "(, " not in p and ", )" not in p

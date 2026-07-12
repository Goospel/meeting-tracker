"""골든 라벨 스키마 + 로더/검증 게이트.

골든 = 완벽한 전사본 + 사람이 라벨한 흐름단절(flag) 4종. 전사 세그먼트와 flag이
양방향으로 일관(세그먼트가 flag을 역참조하고, flag statement가 그 세그먼트에 grounding)해야
한다 — stage-1의 '오프셋 불변식'에 대응하는 stage-2의 무드리프트 방지 게이트.
"""

import json
from pathlib import Path

import pytest

from detect_bench.labels import (
    FlagType,
    FlowFlag,
    flag_from_data,
    load_meeting,
    load_pred_flags,
    meeting_from_data,
    validate_golden,
)

FIX = Path(__file__).resolve().parent.parent / "fixtures"
GOLDEN = FIX / "golden" / "luma_meeting.json"


def _golden_data():
    return json.loads(GOLDEN.read_text(encoding="utf-8-sig"))


# ── 스키마 파싱 ────────────────────────────────────────────────────────────

def test_flag_types_are_the_four_korean_kinds():
    assert {t.value for t in FlagType} == {"모순", "번복", "미해결", "재논의"}


def test_load_meeting_parses_transcript_and_flags():
    m = load_meeting(GOLDEN)
    assert len(m["transcript"]) == 25
    assert len(m["flags"]) == 4
    assert {f.type for f in m["flags"]} == set(FlagType)
    f1 = next(f for f in m["flags"] if f.flag_id == "f1")
    assert f1.type == FlagType.CONTRADICTION
    assert len(f1.statements) == 2
    assert f1.statements[0].speaker == "p2"


def test_transcript_backrefs_parsed_as_tuple():
    m = load_meeting(GOLDEN)
    s14 = next(s for s in m["transcript"] if s.segment_id == "s14")
    assert s14.flags == ("f1",)


def test_load_pred_flags_accepts_list_or_wrapped():
    bare = [{"id": "p1", "type": "모순", "statements": [{"speaker": "p2", "quote": "x"}]}]
    wrapped = {"flags": bare}
    assert len(load_pred_flags_data(bare)) == 1
    assert len(load_pred_flags_data(wrapped)) == 1


def load_pred_flags_data(data):
    # 파일 경유 없이 파싱 재사용을 확인하기 위한 헬퍼 (tmp 파일로 우회 — 반복 실행 시 누적되지 않게 삭제).
    import os
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
        p = fh.name
    try:
        return load_pred_flags(p)
    finally:
        os.unlink(p)


# ── 검증 게이트 ────────────────────────────────────────────────────────────

def test_golden_fixture_validates():
    assert validate_golden(load_meeting(GOLDEN)) is True


def test_unknown_flag_type_raises():
    with pytest.raises(ValueError):
        meeting_from_data({"transcript": [], "flags": [
            {"id": "f1", "type": "헛소리", "statements": [{"speaker": "p1", "quote": "x"}]}]})


def test_duplicate_flag_id_raises():
    data = _golden_data()
    data["flags"].append(dict(data["flags"][0]))  # f1 중복
    with pytest.raises(ValueError):
        validate_golden(meeting_from_data(data))


def test_empty_statements_raises():
    data = _golden_data()
    data["flags"][0]["statements"] = []
    with pytest.raises(ValueError):
        validate_golden(meeting_from_data(data))


def test_backref_to_missing_flag_raises():
    data = _golden_data()
    data["transcript"][0]["flags"] = ["nonexistent"]
    with pytest.raises(ValueError):
        validate_golden(meeting_from_data(data))


def test_golden_quote_not_in_transcript_raises():
    # 골든 flag의 인용이 전사 어디에도 grounding되지 않으면 malformed 골든.
    data = _golden_data()
    data["flags"][0]["statements"][0]["quote"] = "이 문장은 전사본에 존재하지 않습니다 절대로."
    with pytest.raises(ValueError):
        validate_golden(meeting_from_data(data))


def test_backref_inconsistent_with_grounding_raises():
    # flag statement가 grounding된 세그먼트가 그 flag을 역참조하지 않으면 불일치.
    data = _golden_data()
    # s14가 f1을 역참조하는데, 그 역참조를 지우면 f1↔s14 일관성이 깨진다.
    s14 = next(s for s in data["transcript"] if s["id"] == "s14")
    s14["flags"] = []
    with pytest.raises(ValueError):
        validate_golden(meeting_from_data(data))


def test_orphan_backref_raises():
    # [7] 역방향: 세그먼트가 flag을 역참조하는데 그 flag이 이 세그먼트에 grounding되지 않으면 orphan.
    data = _golden_data()
    s1 = next(s for s in data["transcript"] if s["id"] == "s1")
    s1["flags"] = ["f4"]                 # f4는 s5·s6에 grounding — s1엔 아님 → orphan back-ref
    with pytest.raises(ValueError):
        validate_golden(meeting_from_data(data))


def test_duplicate_segment_id_raises():
    # [12] 중복 segment_id는 seg_by_id에서 조용히 붕괴 → 게이트가 잘못된 세그먼트로 평가.
    data = _golden_data()
    data["transcript"].append(dict(data["transcript"][0]))   # s1 중복
    with pytest.raises(ValueError):
        validate_golden(meeting_from_data(data))


# ── 실측 전 보강 ③: 예측 변형 type 라벨 per-flag 강등 / 정규화 ────────────────
# 실제 Claude 출력은 미지 type 라벨(영문·변형)을 낼 수 있다. flag 하나의 변형 라벨이
# run 전체를 중단시키면 안 된다 — 예측은 강등(원문 보존), 골든은 여전히 엄격.

def test_pred_unknown_type_does_not_crash_and_keeps_raw():
    flags = load_pred_flags_data(
        [{"id": "p1", "type": "논리적모순", "statements": [{"speaker": "p2", "quote": "x"}]}])
    assert len(flags) == 1
    assert flags[0].type == "논리적모순"          # 크래시 대신 원문 보존(per-flag 강등)


def test_pred_english_type_is_normalized():
    flags = load_pred_flags_data(
        [{"id": "p1", "type": "reversal", "statements": [{"speaker": "p2", "quote": "x"}]}])
    assert flags[0].type == FlagType.REVERSAL       # 보수적 별칭표로 정규화


def test_golden_unknown_type_still_raises():
    # 골든은 여전히 엄격 — 변형 라벨은 malformed 골든이다(강등 대상 아님).
    with pytest.raises(ValueError):
        meeting_from_data({"transcript": [], "flags": [
            {"id": "f1", "type": "논리적모순", "statements": [{"speaker": "p1", "quote": "x"}]}]})


def test_golden_nfd_korean_type_is_accepted():
    # [리뷰 #7] NFD 분해형/공백 패딩된 정식 한글 유형은 정규화 후 인식돼야 한다(정당한 골든 오거부 방지).
    import unicodedata
    nfd = unicodedata.normalize("NFD", "모순")           # 자모 분해형
    assert nfd != "모순"                                  # 실제로 다른 코드포인트임을 확인
    f = flag_from_data({"id": "f1", "type": f"  {nfd} ",
                        "statements": [{"speaker": "p1", "quote": "x"}]})
    assert f.type == FlagType.CONTRADICTION


def test_golden_english_alias_type_raises():
    # [리뷰 #6/#8] 별칭표는 예측 전용 — 골든에 영문 라벨이 오면 정규화가 아니라 거부여야 한다.
    with pytest.raises(ValueError):
        meeting_from_data({"transcript": [], "flags": [
            {"id": "f1", "type": "reversal", "statements": [{"speaker": "p1", "quote": "x"}]}]})


def test_pred_missing_type_key_does_not_crash():
    # [리뷰1 #11] 예측 flag에 type 키 자체가 없어도 배치 전체를 죽이지 않고 per-flag 강등.
    flags = load_pred_flags_data(
        [{"id": "p1", "type": "모순", "statements": [{"speaker": "p2", "quote": "x"}]},
         {"id": "p2", "statements": [{"speaker": "p3", "quote": "y"}]}])   # type 키 누락
    assert len(flags) == 2                               # 하나 빠졌다고 배치가 죽지 않음
    assert flags[0].type == FlagType.CONTRADICTION


def test_pred_null_quote_does_not_crash():
    # [리뷰2 #3 HIGH] 예측 statement의 quote:null이 NFC normalize에서 TypeError로 배치를 죽이면
    # 안 된다 — 빈 인용으로 강등(불변식상 quote도 신뢰 불가).
    flags = load_pred_flags_data(
        [{"id": "p1", "type": "모순", "statements": [{"speaker": "p2", "quote": None}]}])
    assert len(flags) == 1
    assert flags[0].statements[0].quote == ""


def test_pred_missing_id_degrades():
    # [리뷰2 #5] 예측 flag에 id/flag_id가 둘 다 없어도 배치 전체를 죽이지 않는다(예측 id는 표시용,
    # 채점기는 인덱스 참조). type 강등과 대칭.
    flags = load_pred_flags_data(
        [{"type": "모순", "statements": [{"speaker": "p2", "quote": "x"}]},          # id 없음
         {"id": "p2", "type": "번복", "statements": [{"speaker": "p3", "quote": "y"}]}])
    assert len(flags) == 2


def test_golden_missing_id_raises():
    # 골든은 여전히 엄격 — id 누락은 malformed 골든.
    with pytest.raises(ValueError):
        meeting_from_data({"transcript": [], "flags": [
            {"type": "모순", "statements": [{"speaker": "p1", "quote": "x"}]}]})


def test_pred_null_statements_does_not_crash():
    # [리뷰3] 예측 statements:null(값 존재)이 `for s in None`으로 배치를 죽이면 안 된다 → 빈 목록 강등.
    flags = load_pred_flags_data([{"id": "p1", "type": "모순", "statements": None}])
    assert len(flags) == 1 and flags[0].statements == []


def test_pred_nondict_statement_element_skipped():
    # [리뷰3] statements 리스트의 비-dict 원소(문자열/숫자/null)는 크래시가 아니라 건너뜀.
    flags = load_pred_flags_data(
        [{"id": "p1", "type": "모순",
          "statements": ["그냥 문자열", {"speaker": "p2", "quote": "실제"}, None]}])
    assert len(flags) == 1 and len(flags[0].statements) == 1
    assert flags[0].statements[0].quote == "실제"


def test_pred_nonlist_flags_is_clean_error():
    # [리뷰3] {"flags": null} 같은 구조적 오류는 트레이스백이 아니라 클린 에러(load에서 ValueError).
    with pytest.raises(ValueError):
        load_pred_flags_data({"flags": None})


def test_pred_dict_without_flags_key_is_clean_error():
    # [리뷰4] "flags" 키가 아예 없는 dict 예측도 subscript KeyError가 아니라 디스크립티브 ValueError.
    with pytest.raises(ValueError):
        load_pred_flags_data({"detections": []})


def test_pred_nondict_flag_element_skipped():
    # [리뷰3] 예측 리스트의 비-dict flag 원소는 배치를 죽이지 않고 건너뜀.
    flags = load_pred_flags_data(
        ["그냥 문자열", {"id": "p1", "type": "모순", "statements": [{"speaker": "p2", "quote": "x"}]}])
    assert len(flags) == 1 and flags[0].flag_id == "p1"


def test_golden_null_statements_rejected():
    # 골든의 statements:null은 크래시가 아니라 빈 목록 강등 후 검증 게이트가 거부.
    with pytest.raises(ValueError):
        validate_golden(meeting_from_data({"transcript": [], "flags": [
            {"id": "f1", "type": "모순", "statements": None}]}))


# ── 리뷰4(xhigh): 골든 필드 엄격성·falsy id·클린 에러·NFC 화자 ────────────────

def test_golden_nonstring_quote_raises():
    # [리뷰4 G13] 골든 quote:123/null이 무성으로 ""가 되면 안 된다(main은 로드 시점 에러였음).
    with pytest.raises(ValueError):
        meeting_from_data({"transcript": [], "flags": [
            {"id": "f1", "type": "모순", "statements": [{"speaker": "p1", "quote": 123}]}]})
    with pytest.raises(ValueError):
        meeting_from_data({"transcript": [], "flags": [
            {"id": "f1", "type": "모순", "statements": [{"speaker": "p1", "quote": None}]}]})


def test_golden_nonnumeric_time_sec_raises():
    # [리뷰4 G13] 골든 time_sec 문자열("690")은 힌트가 무성으로 죽는다 — 골든은 엄격 거부.
    with pytest.raises(ValueError):
        meeting_from_data({"transcript": [], "flags": [
            {"id": "f1", "type": "모순",
             "statements": [{"speaker": "p1", "quote": "x", "time_sec": "690"}]}]})


def test_golden_nonnumeric_segment_start_sec_raises():
    # [리뷰4 G1 파생] 골든 세그먼트 start_sec 문자열/null도 엄격 거부(힌트 산술 무성 오염 방지).
    with pytest.raises(ValueError):
        meeting_from_data({"transcript": [
            {"id": "s1", "speaker": "p1", "text": "x", "start_sec": "690"}], "flags": []})
    with pytest.raises(ValueError):
        meeting_from_data({"transcript": [
            {"id": "s1", "speaker": "p1", "text": "x", "start_sec": None}], "flags": []})


def test_golden_id_zero_is_accepted():
    # [리뷰4 G9] id 0은 '존재하는 id'다 — falsy라고 'id가 없음'으로 오거부하면 안 된다.
    f = flag_from_data({"id": 0, "type": "모순",
                        "statements": [{"speaker": "p1", "quote": "x"}]})
    assert f.flag_id == 0


def test_pred_id_zero_is_preserved():
    # [리뷰4 G9] 예측 id 0이 합성 id로 무성 치환되면 원본 추적성이 끊긴다 — str "0"으로 보존.
    flags = load_pred_flags_data(
        [{"id": 0, "type": "모순", "statements": [{"speaker": "p2", "quote": "x"}]}])
    assert flags[0].flag_id == "0"


def test_pred_missing_type_gets_sentinel_not_str_none():
    # [리뷰4 G8] type 키 부재는 str(None)이 지어낸 "None"이 아니라 명시적 센티널로 강등 —
    # 실제로 "None"을 출력한 예측과 리포트에서 구분돼야 한다.
    flags = load_pred_flags_data(
        [{"id": "p1", "statements": [{"speaker": "p2", "quote": "x"}]}])
    assert flags[0].type == "(type 누락)"


def test_golden_null_text_is_clean_error():
    # [리뷰4 G10] 골든 text:null은 TypeError 트레이스백이 아니라 클린 ValueError(CLI rc=2 경로).
    with pytest.raises(ValueError):
        meeting_from_data({"transcript": [
            {"id": "s1", "speaker": "p1", "text": None}], "flags": []})


def test_golden_null_containers_are_clean_error():
    # [리뷰4 G10] transcript/flags:null도 'NoneType 순회' TypeError가 아니라 클린 ValueError.
    with pytest.raises(ValueError):
        meeting_from_data({"transcript": None, "flags": []})
    with pytest.raises(ValueError):
        meeting_from_data({"transcript": [], "flags": None})


def test_golden_nondict_toplevel_is_clean_error():
    # [리뷰4 S1] 골든 자리에 bare-list(예측 파일 스왑 실수)가 오면 AttributeError가 아니라
    # 디스크립티브 ValueError.
    with pytest.raises(ValueError):
        meeting_from_data([{"id": "s1"}])


def test_pred_all_nondict_elements_is_clean_error():
    # [리뷰4 G14] 원소 전량이 비-dict(마크다운 문자열 배열 등)면 '예측 0건'으로 무성 통과가
    # 아니라 클린 에러 — 전량 파싱 실패 run이 정상 리포트로 둔갑하면 벤치 비교가 오염된다.
    with pytest.raises(ValueError):
        load_pred_flags_data(["1. 모순: ...", "2. 번복: ..."])
    assert load_pred_flags_data([]) == []               # 진짜 0건 감지 run은 계속 유효


def test_pred_speaker_is_nfc_normalized():
    # [리뷰4 G12] speaker만 NFC를 우회하면 NFD 화자명 힌트가 무성으로 불발된다.
    import unicodedata
    nfd = unicodedata.normalize("NFD", "김대표")
    flags = load_pred_flags_data(
        [{"id": "p1", "type": "모순",
          "statements": [{"speaker": nfd, "quote": "x"}]}])
    assert flags[0].statements[0].speaker == "김대표"


def test_golden_nonstring_metadata_raises_pred_degrades():
    # [리뷰4 K7] severity/title 등 메타 문자열 필드 — 골든 비문자열은 엄격 거부, 예측은 기본값 강등.
    with pytest.raises(ValueError):
        meeting_from_data({"transcript": [], "flags": [
            {"id": "f1", "type": "모순", "severity": 123,
             "statements": [{"speaker": "p1", "quote": "x"}]}]})
    flags = load_pred_flags_data(
        [{"id": "p1", "type": "모순", "severity": 123, "title": None,
          "statements": [{"speaker": "p2", "quote": "x"}]}])
    assert flags[0].severity == "medium" and flags[0].title == ""


def test_golden_fuzzy_quote_near_boundary_still_validates():
    # [리뷰4 G7a] 인용이 경계를 살짝 넘는(tier-2 퍼지) 골든이 span 확장 탓에 이웃 세그먼트
    # 역참조를 요구받아 오거부되면 안 된다 — 골든 grounding은 단일 세그먼트(main 의미론).
    m = meeting_from_data({
        "meta": {},
        "transcript": [
            {"id": "s5", "speaker": "p1", "text": "예산 초과 문제 우리가", "start_sec": 100,
             "flags": ["f1"]},
            {"id": "s6", "speaker": "p1", "text": "다시 검토합시다", "start_sec": 110},
        ],
        "flags": [
            {"id": "f1", "type": "미해결",
             "statements": [{"speaker": "p1", "quote": "예산 초과 문제 우리가 다시",
                             "time_sec": 100}]},
        ],
    })
    assert validate_golden(m) is True

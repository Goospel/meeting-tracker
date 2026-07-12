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
    # 파일 경유 없이 파싱 재사용을 확인하기 위한 헬퍼 (tmp 파일로 우회).
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
        p = fh.name
    return load_pred_flags(p)


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

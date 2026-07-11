"""적대적 코드리뷰(wf_3af2c696-099)에서 확정된 버그의 회귀 테스트.

각 테스트는 리뷰 finding 번호(F#)에 대응한다. 전부 1순위 KPI(CTER) 경로에서
'조용히 틀린 값'을 내던 실제 결함이다.
"""

import json
from pathlib import Path

import pytest

from stt_bench.entities import CriticalEntity, EntityType
from stt_bench.golden import load_golden, validate_golden
from stt_bench.korean_datetime import parse_date, parse_time
from stt_bench.korean_numbers import parse_number
from stt_bench.report import score_meeting
from stt_bench.score import score_clip

FIX = Path(__file__).resolve().parent.parent / "fixtures"


# ── F1: '일' 날짜 마커가 한자어 숫자 1(일)과 충돌 ──────────────────────────
@pytest.mark.parametrize(
    "text,expected",
    [
        ("이십일일", {"day": 21}),
        ("십일일", {"day": 11}),
        ("삼십일일", {"day": 31}),
        ("일일", {"day": 1}),
        ("십일월", {"month": 11}),   # 유령 day 없어야
    ],
)
def test_f1_day_marker_vs_sino_one(text, expected):
    assert parse_date(text) == expected


# ── F2/F7/F9: 고유어 합성 수사 (11/12시, 스물셋 등) ────────────────────────
@pytest.mark.parametrize(
    "text,val",
    [("열둘", 12), ("열두", 12), ("열한", 11), ("스물셋", 23),
     ("스물세", 23), ("서른다섯", 35), ("마흔둘", 42)],
)
def test_f2_native_compound_number(text, val):
    r = parse_number(text)
    assert r.kind == "value" and r.value == val


def test_f2_native_compound_with_unit():
    r = parse_number("스물세 명")
    assert r.kind == "value" and r.value == 23 and r.unit == "명"


@pytest.mark.parametrize("text,expected", [("열두 시", {"hour": 12}), ("열한 시", {"hour": 11})])
def test_f2_native_compound_hour(text, expected):
    assert parse_time(text) == expected


# ── F3: _project_span 과대 스팬 → 값 반전(sub)이 삭제(del)로 오분류 ────────
def test_f3_overspan_reversal_is_sub_not_del():
    ref = "사천만원으로 합시다"
    hyp = "사실은 오천만원으로 합시다"   # 4천만→5천만 반전 + 선행 '사' 쌍둥이
    cs = ref.index("사천만원")
    ent = CriticalEntity("e1", EntityType.AMOUNT, cs, cs + 4, "사천만원", {"value": 40_000_000, "unit": "KRW"})
    s = score_clip(ref, [ent], hyp)
    assert s.entity_scores[0].outcome == "value_mismatch"
    assert len(s.false_contradiction_candidates) == 1
    assert len(s.missed_token_candidates) == 0


# ── F4: 화이트리스트 밖 단위 / 소수점 ────────────────────────────────────
def test_f4_unit_outside_whitelist_is_hit():
    ref = "세 번 갔어요"
    hyp = "3번 갔어요"
    cs = ref.index("세 번")
    ent = CriticalEntity("e1", EntityType.UNIT_QUANTITY, cs, cs + len("세 번"), "세 번", {"value": 3, "unit": "번"})
    s = score_clip(ref, [ent], hyp)
    assert s.per_type["UNIT_QUANTITY"].hit == 1


def test_f4_decimal_percent():
    r = parse_number("3.5%")
    assert r.kind == "value" and r.value == 3.5


# ── F5/F12: PROPER_NOUN 조사 동형 말미 오버스트립 ─────────────────────────
@pytest.mark.parametrize("noun", ["오메가", "지은", "종로"])
def test_f5_proper_noun_not_overstripped(noun):
    ref = f"{noun} 관련 회의입니다"
    hyp = ref  # 완벽 인식
    cs = ref.index(noun)
    ent = CriticalEntity("e1", EntityType.PROPER_NOUN, cs, cs + len(noun), noun, {"canonical": noun})
    s = score_clip(ref, [ent], hyp)
    assert s.per_type["PROPER_NOUN"].hit == 1
    assert len(s.false_contradiction_candidates) == 0


def test_f5_real_particle_still_stripped():
    ref = "재무팀과 확정했어요"
    cs = ref.index("재무팀")
    ent = CriticalEntity("e1", EntityType.PROPER_NOUN, cs, cs + 3, "재무팀", {"canonical": "재무팀"})
    s = score_clip(ref, [ent], "재무팀과 확정했어요")
    assert s.per_type["PROPER_NOUN"].hit == 1


# ── F6: 세그먼트 id 조인 실패 감지 ───────────────────────────────────────
def test_f6_segment_id_mismatch_raises():
    g = load_golden(FIX / "golden" / "budget_meeting.json")
    bad_hyp = {
        "clip_id": "budget_meeting_demo",
        "provider": "x",
        "segments": {  # 골든 id(s1/s2)와 완전히 다른 provider-native id
            "seg_0": "예산은 3천만원까지 무리 없이 쓸 수 있어요",
            "seg_1": "8월 셋째 주 출시로 확정합시다",
        },
    }
    with pytest.raises(ValueError):
        score_meeting(g, bad_hyp)


# ── F8: 시간(duration) != 시(clock) ─────────────────────────────────────
def test_f8_duration_is_not_clock_time():
    assert parse_time("두 시간") is None
    assert parse_time("한 시간 반") is None


# ── F11: 오전/오후(AM/PM) 반전 감지 ─────────────────────────────────────
def test_f11_meridiem_reversal_detected():
    ref = "오후 3시에 시작합시다"
    hyp = "오전 3시에 시작합시다"
    cs = ref.index("오후 3시")
    ent = CriticalEntity("e1", EntityType.TIME, cs, cs + len("오후 3시"), "오후 3시", {"hour": 3, "meridiem": "pm"})
    s = score_clip(ref, [ent], hyp)
    assert s.entity_scores[0].outcome == "value_mismatch"


# ── F13: 불규칙 월 표기 유월(6)/시월(10) ─────────────────────────────────
@pytest.mark.parametrize("text,month", [("시월", 10), ("유월", 6)])
def test_f13_irregular_month(text, month):
    assert parse_date(text) == {"month": month}


# ── F10: DATE 과소명세 골든을 검증 게이트가 거부 ──────────────────────────
def test_f10_validate_rejects_underspecified_date(tmp_path):
    bad = {
        "clip_id": "x",
        "segments": [
            {
                "segment_id": "s",
                "speaker": "p1",
                "start_sec": 0,
                "end_sec": 1,
                "text": "8월 15일 출시",
                "critical_entities": [
                    {"entity_id": "e", "type": "DATE", "char_start": 0, "char_end": 6,
                     "surface": "8월 15일", "canonical": {"month": 8}},  # day 누락
                ],
            }
        ],
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_golden(load_golden(p))

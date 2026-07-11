"""xhigh 코드리뷰(review-xhigh-fix-handoff.md)에서 실행 재현 확정된 결함 R1~R15의
회귀 테스트. F1~F13 패턴을 잇는다. 각 테스트는 지시서 재현 스니펫의 '기대' 동작.

2순위(S#) 중 같은 계통이라 함께 처리한 것도 여기 포함한다.
"""

import json
from pathlib import Path

import pytest

from stt_bench.entities import CriticalEntity, EntityType
from stt_bench.golden import load_golden, validate_golden
from stt_bench.korean_datetime import parse_date, parse_time
from stt_bench.korean_numbers import parse_number
from stt_bench.report import render_report, score_meeting
from stt_bench.score import score_clip


def _golden_from(segments):
    """세그먼트 dict 리스트로 임시 골든 JSON을 만들어 로드."""
    data = {"clip_id": "t", "segments": segments}
    import tempfile
    import os
    fd, p = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    Path(p).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return load_golden(p)


def _amount(cs, surface, value, unit="KRW"):
    return CriticalEntity("e", EntityType.AMOUNT, cs, cs + len(surface), surface,
                          {"value": value, "unit": unit})


# ── R1: 수·단위 동형 문자 경계 붕괴 ─────────────────────────────────────
def test_r1_arabic_then_sino_is_unit_boundary():
    r = parse_number("3일")
    assert r.value == 3 and r.unit == "일"


def test_r1_ten_days():
    r = parse_number("10일")
    assert r.value == 10 and r.unit == "일"


def test_r1_native_syllable_unit_not_truncated():
    r = parse_number("5마리")
    assert r.kind == "value" and r.value == 5 and r.unit == "마리"


def test_r1_e2e_unit_quantity_day_is_hit():
    ref = "총 3일 걸립니다"
    cs = ref.index("3일")
    ent = CriticalEntity("e", EntityType.UNIT_QUANTITY, cs, cs + 2, "3일", {"value": 3, "unit": "일"})
    s = score_clip(ref, [ent], ref)
    assert s.per_type["UNIT_QUANTITY"].hit == 1


# ── R2: 숫자-마커 사이 공백에서 필드 소실 ────────────────────────────────
def test_r2_space_between_number_and_minute():
    assert parse_time("3시 40 분") == {"hour": 3, "minute": 40}


def test_r2_space_between_number_and_day():
    assert parse_date("8월 15 일") == {"month": 8, "day": 15}


# ── R3: DATE/TIME 비교 양방향 ────────────────────────────────────────────
def test_r3_hallucinated_field_is_mismatch():
    # hyp가 없던 '15일'을 삽입 → value_mismatch (무성 hit 아님)
    ref = "8월 말 출시 예정입니다"
    cs = ref.index("8월 말")
    ent = CriticalEntity("e", EntityType.DATE, cs, cs + 4, "8월 말", {"month": 8, "part": "말"})
    s = score_clip(ref, [ent], "8월 15일 말 출시 예정입니다")
    assert s.entity_scores[0].outcome == "value_mismatch"


# ── R4: AMOUNT 단위(통화) 대조 ───────────────────────────────────────────
def test_r4_currency_reversal_is_mismatch():
    ref = "예산은 3천만원까지요"
    ent = _amount(ref.index("3천만원"), "3천만원", 30_000_000, "KRW")
    s = score_clip(ref, [ent], "예산은 3천만 달러까지요")
    assert s.entity_scores[0].outcome == "value_mismatch"


def test_r4_same_currency_still_hit():
    ref = "예산은 3천만원까지요"
    ent = _amount(ref.index("3천만원"), "3천만원", 30_000_000, "KRW")
    assert score_clip(ref, [ent], ref).entity_scores[0].outcome == "hit"


# ── R5: salvage/파싱이 무관 숫자를 주워 del→sub 반전 ─────────────────────
def test_r5_unrelated_number_stays_deleted():
    ref = "예산은 3천만원까지 씁니다"
    ent = _amount(ref.index("3천만원"), "3천만원", 30_000_000, "KRW")
    s = score_clip(ref, [ent], "예산은 2시까지 씁니다")   # 금액 소실 + 근처 시각
    assert s.entity_scores[0].outcome == "deleted"


# ── R6: '시' 마커 선점 + 유령 hour ──────────────────────────────────────
def test_r6_clock_marker_not_preempted_by_word():
    assert parse_time("시작은 3시") == {"hour": 3}


def test_r6_no_phantom_hour_from_word():
    # '오후 시작': hour 없어야(오=5 유령 금지), meridiem만
    assert parse_time("오후 시작") == {"meridiem": "pm"}


# ── R7: '일' 마커가 요일·복합어에 앵커 ──────────────────────────────────
def test_r7_day_not_anchored_to_weekday():
    assert parse_date("8월 15일 월요일") == {"month": 8, "day": 15}


def test_r7_day_not_anchored_to_compound():
    assert parse_date("8월 20일 마감일") == {"month": 8, "day": 20}


# ── R8: _PART 부분문자열 유령 part ──────────────────────────────────────
def test_r8_no_phantom_part_without_date_context():
    assert parse_date("정말 좋아요") is None
    assert parse_date("초안 검토") is None


def test_r8_real_part_still_works():
    assert parse_date("9월 초") == {"month": 9, "part": "초"}


# ── R9: 명시적 범위 공유 접미 분배 ──────────────────────────────────────
def test_r9_range_shared_suffix():
    r = parse_number("2~3천만")
    assert (r.kind, r.low, r.high) == ("range", 20_000_000, 30_000_000)


# ── R10: 스팬 경계 삽입 탈락으로 범위화 은폐 ────────────────────────────
def test_r10_range_tampering_not_hit():
    ref = "예산은 3천만원으로 확정"
    ent = _amount(ref.index("3천만원"), "3천만원", 30_000_000, "KRW")
    s = score_clip(ref, [ent], "예산은 이삼천만원으로 확정")   # 단일값→범위 변조
    assert s.entity_scores[0].outcome != "hit"


# ── R11: 구어 범위 구분자 + RANGE salvage ───────────────────────────────
def test_r11_spoken_range_separator():
    ref = "이천만에서 삼천만원 사이로 잡죠"
    surface = "이천만에서 삼천만원"
    cs = ref.index(surface)
    ent = CriticalEntity("e", EntityType.RANGE, cs, cs + len(surface), surface,
                         {"low": 20_000_000, "high": 30_000_000})
    assert score_clip(ref, [ent], ref).entity_scores[0].outcome == "hit"


# ── R12: 검증 게이트가 파서 출력을 진리로 삼는 순환 ─────────────────────
def test_r12_numeric_canonical_surface_mismatch_rejected():
    g = _golden_from([{
        "segment_id": "s", "speaker": "p", "start_sec": 0, "end_sec": 1,
        "text": "예산 3천만원",
        "critical_entities": [{"entity_id": "e", "type": "AMOUNT", "char_start": 3, "char_end": 7,
                               "surface": "3천만원", "canonical": {"value": 20_000_000, "unit": "KRW"}}],
    }])
    with pytest.raises(ValueError):
        validate_golden(g)


def test_r12_date_extra_key_rejected():
    # R3①: 파서가 산출 못 하는 여분 키(day) → 게이트 사전 거부
    g = _golden_from([{
        "segment_id": "s", "speaker": "p", "start_sec": 0, "end_sec": 1,
        "text": "8월에 출시",
        "critical_entities": [{"entity_id": "e", "type": "DATE", "char_start": 0, "char_end": 2,
                               "surface": "8월", "canonical": {"month": 8, "day": 15}}],
    }])
    with pytest.raises(ValueError):
        validate_golden(g)


def test_r12_manual_optout_allows_unparseable():
    # '정오'(TIME hour 12)는 파서 밖 표기 — flags.manual로 등록 허용
    g = _golden_from([{
        "segment_id": "s", "speaker": "p", "start_sec": 0, "end_sec": 1,
        "text": "정오에 만나요",
        "critical_entities": [{"entity_id": "e", "type": "TIME", "char_start": 0, "char_end": 2,
                               "surface": "정오", "canonical": {"hour": 12}, "flags": {"manual": True}}],
    }])
    assert validate_golden(g) is True


# ── R13: 조인 계약 — 환각 세그먼트 + clip_id ────────────────────────────
def test_r13_extra_hyp_segment_surfaced():
    g = load_golden(Path(__file__).resolve().parent.parent / "fixtures" / "golden" / "budget_meeting.json")
    hyp = {
        "clip_id": "budget_meeting_demo", "provider": "x",
        "segments": {
            "s1": "예산은 3천만원까지 무리 없이 쓸 수 있어요",
            "s2": "8월 셋째 주 출시로 확정합시다",
            "s3": "예산을 5천만원으로 올리는 걸로 하죠",   # 환각 삽입
        },
    }
    m = score_meeting(g, hyp)
    assert "s3" in m.get("extra_segments", [])


def test_r13_clip_id_mismatch_raises():
    g = load_golden(Path(__file__).resolve().parent.parent / "fixtures" / "golden" / "budget_meeting.json")
    with pytest.raises(ValueError):
        score_meeting(g, {"clip_id": "다른회의", "provider": "x", "segments": {"s1": "아무거나"}})


# ── R14: hyp 세그먼트 0개(완전 실패)는 크래시 아님 ─────────────────────
def test_r14_empty_hyp_scores_all_deleted():
    g = load_golden(Path(__file__).resolve().parent.parent / "fixtures" / "golden" / "budget_meeting.json")
    m = score_meeting(g, {"clip_id": "budget_meeting_demo", "provider": "x", "segments": {}})
    assert m["per_type"]["AMOUNT"].cter == 1.0   # 전부 삭제로 채점
    assert m.get("total_failure") is True


# ── R15: ambiguous가 리포트·불변식에서 가시 ─────────────────────────────
def test_r15_ambiguous_visible():
    ref = "이삼천만원으로 잡죠"
    surface = "이삼천만원"
    cs = ref.index(surface)
    ent = CriticalEntity("e", EntityType.RANGE, cs, cs + len(surface), surface,
                         {"low": 20_000_000, "high": 30_000_000})
    s = score_clip(ref, [ent], "이천만원으로 잡죠")
    agg = s.per_type["RANGE"]
    assert s.entity_scores[0].outcome == "ambiguous"
    assert agg.ambiguous == 1
    # 불변식: hit+sub+del+ambig == n
    assert agg.hit + agg.sub + agg.deleted + agg.ambiguous == agg.n


def test_r15_report_has_ambig_column():
    g = load_golden(Path(__file__).resolve().parent.parent / "fixtures" / "golden" / "budget_meeting.json")
    hyp = {"clip_id": "budget_meeting_demo", "provider": "x",
           "segments": {"s1": "예산은 3천만원까지 무리 없이 쓸 수 있어요", "s2": "8월 셋째 주 출시로 확정합시다"}}
    md = render_report(g, hyp, score_meeting(g, hyp))
    assert "ambig" in md


# ── S2: '분'도 지속시간('간') 가드 (탐색기 일반화의 부산물) ──────────────
def test_s2_minute_duration_guard():
    assert parse_time("30분간") is None


# ── S5: 전각 숫자 ───────────────────────────────────────────────────────
def test_s5_fullwidth_percent():
    r = parse_number("３０％")
    assert r.value == 30 and r.unit == "%"


# ── S6: 소수+큰단위 / 한글 소수 ─────────────────────────────────────────
def test_s6_decimal_with_big_unit():
    assert parse_number("3.5억").value == 350_000_000


def test_s6_korean_decimal():
    assert parse_number("삼점오").value == 3.5


# ── S9: 중복 segment_id 검증 ────────────────────────────────────────────
def test_s9_duplicate_segment_id_rejected():
    g = _golden_from([
        {"segment_id": "s", "speaker": "p", "start_sec": 0, "end_sec": 1, "text": "가", "critical_entities": []},
        {"segment_id": "s", "speaker": "p", "start_sec": 1, "end_sec": 2, "text": "나", "critical_entities": []},
    ])
    with pytest.raises(ValueError):
        validate_golden(g)


# ── S10: 조사 분리 경로를 실제로 타는 테스트 ────────────────────────────
def test_s10_particle_stripping_path_covered():
    # _strip_particles가 identity면 실패해야 하는 케이스 (조사 결합형만 hit)
    ref = "종로에서 만나요"
    cs = ref.index("종로")
    ent = CriticalEntity("e", EntityType.PROPER_NOUN, cs, cs + 2, "종로", {"canonical": "종로"})
    # hyp가 '종로에서'로 조사 결합 → 스팬 '종로에서', 조사 분리 후 '종로'==canonical
    s = score_clip(ref, [ent], "종로에서 만나요")
    assert s.per_type["PROPER_NOUN"].hit == 1

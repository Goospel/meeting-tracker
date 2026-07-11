"""CTER(Critical Token Error Rate) 엔티티 채점기 테스트 — 이 제품의 1순위 KPI.

핵심 논지(플래그십 테스트가 증명):
  전체 CER이 낮아도 "치명 토큰(금액·날짜·고유명사…)"이 틀리면 제품은 실패한다.
  두 지표가 갈리는 지점을 회귀로 고정한다.

sub(값 치환) = 가짜 모순 후보(false_contradiction), del(삭제) = 놓친 모순 후보
(missed_token). 이 둘은 제품 피해가 달라 반드시 분리 집계한다.
"""

from stt_bench.entities import CriticalEntity, EntityType
from stt_bench.score import score_clip


def _amount(entity_id, ref, surface, value):
    cs = ref.index(surface)
    return CriticalEntity(
        entity_id=entity_id,
        type=EntityType.AMOUNT,
        char_start=cs,
        char_end=cs + len(surface),
        surface=surface,
        canonical={"value": value, "unit": "KRW"},
    )


def test_flagship_low_global_cer_but_high_entity_error():
    ref = "예산은 3천만원까지 무리 없이 쓸 수 있어요"
    hyp = "예산은 2천만원까지 무리 없이 쓸 수 있어요"
    ents = [_amount("e1", ref, "3천만원", 30_000_000)]

    s = score_clip(ref, ents, hyp)

    # 전체 CER은 낮다 (1자 차이)
    assert s.cer.raw < 0.1
    # 그러나 치명 토큰은 완전 실패
    agg = s.per_type["AMOUNT"]
    assert agg.n == 1
    assert agg.sub == 1
    assert agg.sub_rate == 1.0
    assert agg.cter == 1.0
    # 값 치환 → 가짜 모순 후보 1건
    assert len(s.false_contradiction_candidates) == 1
    fc = s.false_contradiction_candidates[0]
    assert fc.ref_value == 30_000_000
    assert fc.hyp_value == 20_000_000


def test_benign_surface_form_is_not_an_error():
    # "3천만원" -> "삼천만원": 값 등가라 오류 아님(가짜 오류로 세면 안 됨).
    ref = "예산은 3천만원까지 가능합니다"
    hyp = "예산은 삼천만원까지 가능합니다"
    ents = [_amount("e1", ref, "3천만원", 30_000_000)]

    s = score_clip(ref, ents, hyp)

    agg = s.per_type["AMOUNT"]
    assert agg.hit == 1
    assert agg.sub == 0
    assert len(s.false_contradiction_candidates) == 0


def test_deleted_amount_is_missed_not_substituted():
    # 금액이 "그 정도"로 뭉개짐 = 삭제(놓친 모순), 치환과 분리 집계.
    ref = "예산은 3천만원까지 가능해요"
    hyp = "예산은 그 정도까지 가능해요"
    ents = [_amount("e1", ref, "3천만원", 30_000_000)]

    s = score_clip(ref, ents, hyp)

    agg = s.per_type["AMOUNT"]
    assert agg.deleted == 1
    assert agg.sub == 0
    assert agg.del_rate == 1.0
    assert len(s.missed_token_candidates) == 1
    assert len(s.false_contradiction_candidates) == 0


def test_entity_correct_despite_noisy_surroundings():
    # test_cer의 고CER 케이스의 대칭: 주변은 시끄러워도 핵심 금액은 정확 → CTER = 0.
    ref = "그 예산은요 3천만원까지 가능하다고 저는 봅니다"
    hyp = "음 그 예산은 어 3천만원 까지 가능하다구 뭐 봅니다"
    ents = [_amount("e1", ref, "3천만원", 30_000_000)]

    s = score_clip(ref, ents, hyp)

    assert s.cer.raw > 0.2          # 전체 CER은 높다
    agg = s.per_type["AMOUNT"]
    assert agg.hit == 1
    assert agg.cter == 0.0          # 치명 토큰은 깨끗


def test_date_reversal_detected():
    ref = "8월 셋째 주 출시로 확정합시다"
    hyp = "9월 셋째 주 출시로 확정합시다"
    cs = ref.index("8월 셋째 주")
    ent = CriticalEntity(
        "e1", EntityType.DATE, cs, cs + len("8월 셋째 주"), "8월 셋째 주",
        {"month": 8, "week_of_month": 3},
    )

    s = score_clip(ref, [ent], hyp)

    agg = s.per_type["DATE"]
    assert agg.sub == 1
    assert agg.sub_rate == 1.0


def test_proper_noun_error_with_particle_stripping():
    # "루미"->"누미" = 오류. "재무팀과"는 조사(과) 분리 후 "재무팀"==정답 → 정상.
    ref = "루미 출시는 재무팀과 확정했어요"
    hyp = "누미 출시는 재무팀과 확정했어요"
    cs1 = ref.index("루미")
    cs2 = ref.index("재무팀")
    ents = [
        CriticalEntity("e1", EntityType.PROPER_NOUN, cs1, cs1 + 2, "루미", {"canonical": "루미"}),
        CriticalEntity("e2", EntityType.PROPER_NOUN, cs2, cs2 + 3, "재무팀", {"canonical": "재무팀"}),
    ]

    s = score_clip(ref, ents, hyp)

    agg = s.per_type["PROPER_NOUN"]
    assert agg.n == 2
    assert agg.sub == 1   # 루미->누미
    assert agg.hit == 1   # 재무팀 (조사 분리 후 일치)


def test_unit_quantity_native_numeral_is_benign():
    # "세 편"(고유어 수관형사) vs "3편": 값 등가 → 오류 아님.
    ref = "숏폼 세 편 만들기로 했어요"
    hyp = "숏폼 3편 만들기로 했어요"
    cs = ref.index("세 편")
    ent = CriticalEntity(
        "e1", EntityType.UNIT_QUANTITY, cs, cs + len("세 편"), "세 편",
        {"value": 3, "unit": "편"},
    )

    s = score_clip(ref, [ent], hyp)

    agg = s.per_type["UNIT_QUANTITY"]
    assert agg.hit == 1
    assert agg.sub == 0

"""전체 CER(문자오류율) 테스트.

핵심: 전체 CER은 "새너티/맥락용 보조지표"다. 아래 테스트는 전체 CER이 왜 이 제품의
1순위 지표가 될 수 없는지(치명 토큰 오류를 과소평가)를 드러낸다 — 진짜 KPI는
test_score.py의 CTER(엔티티 집중 오류율)다.
"""

from stt_bench.cer import cer


def test_cer_low_for_single_char_number_swap():
    # "3천만원" -> "2천만원": 딱 1자 차이라 전체 CER은 매우 낮다.
    # (그런데 이건 예산 3천만->2천만, 제품상 치명적 — CTER은 이걸 100%로 잡는다.)
    r = cer(
        "예산은 3천만원까지 무리 없이 쓸 수 있어요",
        "예산은 2천만원까지 무리 없이 쓸 수 있어요",
    )
    assert r.raw < 0.1


def test_cer_raw_positive_for_surface_form_diff():
    # "3천만원" vs "삼천만원": 값은 같지만 표면형이 달라 raw CER > 0.
    # (전체 CER은 정당한 표면 변형도 오류로 셈 — 그래서 값 등가는 엔티티 레벨에서 본다.)
    r = cer("예산은 3천만원까지 가능합니다", "예산은 삼천만원까지 가능합니다")
    assert r.raw > 0


def test_cer_high_for_filler_and_spacing_noise():
    # 필러("음","어","뭐")·어미·띄어쓰기 오차가 많으면 전체 CER은 높다 —
    # 하지만 핵심 금액은 정확할 수 있다(test_score의 역방향 케이스).
    r = cer(
        "그 예산은요 3천만원까지 가능하다고 저는 봅니다",
        "음 그 예산은 어 3천만원 까지 가능하다구 뭐 봅니다",
    )
    assert r.raw > 0.2


def test_cer_empty_reference():
    # 빈 레퍼런스: 0-division 회피. rate는 미정의로 두되 원시 삽입수를 보고.
    r = cer("", "네 그렇습니다")
    assert r.n_ref == 0
    assert r.insertions > 0


def test_cer_outlier_when_greater_than_one():
    # 환각형 삽입 다수 → CER > 1.0 가능 → outlier 플래그(평균 오염 방지).
    r = cer("네", "네 네 네 그러니까 저 음 그")
    assert r.raw > 1.0
    assert r.outlier is True


def test_normalized_cer_keeps_spacing_significant():
    # 의미전복 최소대립: "잘 못"(부정) vs "잘못"(과오).
    # 공백 완전제거(N3)를 하면 이 차이가 사라지므로 N3는 상시 비활성 —
    # 정규화 CER은 공백을 single로 collapse하되 유지하므로 여전히 > 0.
    r = cer("그건 잘 못 했어요", "그건 잘못했어요")
    assert r.norm > 0


def test_normalized_cer_collapses_redundant_whitespace():
    # 중복 공백/양끝 공백만 다르면 정규화 CER은 0 (표면 잡음 흡수).
    r = cer("예산  3천만원", " 예산 3천만원 ")
    assert r.norm == 0
    assert r.raw > 0  # 원본은 공백 차이로 > 0

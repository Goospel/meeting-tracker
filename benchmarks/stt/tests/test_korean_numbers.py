"""한국어 수 파서 단위 테스트.

제품 관점: STT가 "3천만원"을 "2천만원"으로 오인식하면 모순감지 입력이 오염된다.
이 파서는 표면형이 달라도(3천만=삼천만=30,000,000) 같은 값이면 같게, 값이 다르면
다르게 보는 "값 등가" 비교의 토대다.
"""

import pytest

from stt_bench.korean_numbers import parse_number


@pytest.mark.parametrize(
    "text,expected",
    [
        ("3천만원", 30_000_000),      # 아라비아 + 한자어 자릿수
        ("삼천만원", 30_000_000),     # 순수 한자어
        ("30,000,000원", 30_000_000),  # 아라비아 + 콤마
        ("3,000만원", 30_000_000),     # 혼합 + 콤마
        ("2천만원", 20_000_000),
        ("3000", 3000),
        ("삼천이백", 3200),
        ("만원", 10_000),            # 암묵 1 (만 = 1만)
        ("오천", 5000),
        ("십이만삼천", 123_000),
        ("일억", 100_000_000),
    ],
)
def test_parse_number_value(text, expected):
    r = parse_number(text)
    assert r.kind == "value"
    assert r.value == expected


@pytest.mark.parametrize(
    "text,val",
    [
        ("세 편", 3),   # 고유어 수관형사 세 = 3
        ("세", 3),
        ("다섯", 5),
        ("스무", 20),
        ("두", 2),
    ],
)
def test_parse_native_numeral(text, val):
    r = parse_number(text)
    assert r.kind == "value"
    assert r.value == val


def test_parse_range_not_silently_absorbed():
    # "이삼천만원" = 2천만~3천만. 단일 value로 뭉개거나 None(→del)으로 흘리면 안 되고
    # range 타입으로 분리돼야 한다(모순 함의가 단일값과 다름).
    r = parse_number("이삼천만원")
    assert r.kind == "range"
    assert r.low == 20_000_000
    assert r.high == 30_000_000


def test_parse_hedge_flagged_but_value_kept():
    # "약 ~ 정도" 헤지는 벗겨 값은 얻되, 헤지가 있었다는 사실을 플래그로 보존
    # (근사치가 확정치로 둔갑하는 위험 노출용).
    r = parse_number("약 3천만원 정도")
    assert r.kind == "value"
    assert r.value == 30_000_000
    assert r.hedge is True


def test_parse_none_for_non_number():
    r = parse_number("그 정도")
    assert r.kind == "none"
    assert r.value is None


def test_parse_unit_captured():
    r = parse_number("3편")
    assert r.kind == "value"
    assert r.value == 3
    assert r.unit == "편"

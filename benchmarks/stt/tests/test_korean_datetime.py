"""날짜/시각 파서 단위 테스트.

제품 관점: "8월 출시 확정"이 "9월"로 번복되는 것을 잡으려면 날짜를 구조화된 값으로
비교해야 한다(표면형 비교로는 "8월 셋째 주"='8월'을 놓친다).
"""

from stt_bench.korean_datetime import parse_date, parse_time


def test_parse_date_month_and_week():
    assert parse_date("8월 셋째 주") == {"month": 8, "week_of_month": 3}


def test_parse_date_month_only():
    assert parse_date("9월") == {"month": 9}


def test_parse_date_part():
    assert parse_date("9월 초") == {"month": 9, "part": "초"}


def test_parse_date_day():
    assert parse_date("3월 14일") == {"month": 3, "day": 14}


def test_parse_date_none():
    assert parse_date("다음 주") is None  # 상대 표현은 값 정규화하지 않음


def test_parse_time_hour_native():
    assert parse_time("세 시") == {"hour": 3}


def test_parse_time_half():
    assert parse_time("다섯 시 반") == {"hour": 5, "minute": 30}


def test_parse_time_minute():
    assert parse_time("3시 40분") == {"hour": 3, "minute": 40}

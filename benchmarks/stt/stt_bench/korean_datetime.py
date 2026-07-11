"""날짜/시각 파서 — 구조화된 값 비교.

'8월 출시 확정'이 '9월'로 번복되는 것을 잡으려면 날짜를 {month, week_of_month, ...}
구조로 비교해야 한다. 상대 표현('다음 주')은 값 정규화하지 않는다(맥락 필요).

주의(코드리뷰 반영):
  - '일'은 날짜 마커이자 한자어 숫자 1이라, 월을 먼저 떼어낸 뒤 남은 부분에서
    rfind로 '일'을 찾아 일(日) 숫자를 온전히 읽는다(F1).
  - '유월'(6월)·'시월'(10월)은 불규칙 표기라 별도 매핑(F13).
  - '시'는 '시간'(지속시간)과 구분한다(F8), 오전/오후를 추출한다(F11),
    '열두 시'(12시) 같은 고유어 합성 시각은 parse_number가 처리한다(F2).
"""

from __future__ import annotations

import unicodedata

from .korean_numbers import SINO_DIGIT, SMALL_UNIT, parse_number

_WEEK = {"첫째": 1, "둘째": 2, "셋째": 3, "넷째": 4, "다섯째": 5}
_PART = ("초", "중순", "말")
_IRREGULAR_MONTH = {"유월": 6, "시월": 10}
_NUMCHARS = set("0123456789,") | set(SINO_DIGIT) | set(SMALL_UNIT)


def _num_walk_left(s: str, end: int) -> int | None:
    """s[end] 바로 앞의 숫자 표현(한자어/아라비아)을 정수로."""
    k = end
    chars = []
    while k > 0 and s[k - 1] in _NUMCHARS:
        chars.append(s[k - 1])
        k -= 1
    if not chars:
        return None
    r = parse_number("".join(reversed(chars)))
    return int(r.value) if r.kind == "value" and r.value is not None else None


def _extract_month(s: str) -> tuple[int | None, str]:
    """(month|None, '월' 이후 나머지 문자열)."""
    for word, val in _IRREGULAR_MONTH.items():
        i = s.find(word)
        if i >= 0:
            return val, s[i + len(word):]
    i = s.find("월")
    if i >= 0:
        return _num_walk_left(s, i), s[i + 1:]
    return None, s


def parse_date(text: str) -> dict | None:
    """날짜를 {month, day?, week_of_month?, part?}로. 못 잡으면 None."""
    s = unicodedata.normalize("NFC", text).strip()
    out: dict = {}

    month, rest = _extract_month(s)
    if month is not None:
        out["month"] = month

    # 일(日)은 월을 뗀 나머지에서 rfind로 마커를 찾고, 그 마커 '앞'의 숫자만 읽는다
    # — 마커 '일'(=1) 자체를 숫자에 넣으면 21일이 211로 새어버린다(F1).
    di = rest.rfind("일")
    if di >= 0:
        day = _num_walk_left(rest, di)
        if day is not None:
            out["day"] = day

    if "주" in s:
        for word, val in _WEEK.items():
            if word in s:
                out["week_of_month"] = val
                break

    for part in _PART:
        if part in s:
            out["part"] = part
            break

    return out or None


def _find_clock_si(s: str) -> int:
    """시각의 '시' 인덱스. '시간'(지속시간)의 '시'는 건너뛴다."""
    i = s.find("시")
    while i != -1:
        if i + 1 >= len(s) or s[i + 1] != "간":
            return i
        i = s.find("시", i + 1)
    return -1


def parse_time(text: str) -> dict | None:
    """시각을 {hour?, minute?, meridiem?}로. 못 잡으면 None."""
    s = unicodedata.normalize("NFC", text).strip()
    out: dict = {}

    i = _find_clock_si(s)
    if i >= 0:
        left = s[:i].strip()
        tok = left.split()[-1] if left.split() else ""
        r = parse_number(tok)   # 고유어 합성(열두=12) 포함
        if r.kind == "value" and r.value is not None:
            out["hour"] = int(r.value)
        else:
            hour = _num_walk_left(s, i)
            if hour is not None:
                out["hour"] = hour

    mi = s.find("분")
    minute = _num_walk_left(s, mi) if mi >= 0 else None
    if minute is not None:
        out["minute"] = minute
    elif "반" in s and "hour" in out:
        out["minute"] = 30

    if "오후" in s:
        out["meridiem"] = "pm"
    elif "오전" in s:
        out["meridiem"] = "am"

    return out or None

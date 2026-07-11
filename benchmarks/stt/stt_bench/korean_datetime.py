"""날짜/시각 파서 — 구조화된 값 비교.

'8월 출시 확정'이 '9월'로 번복되는 것을 잡으려면 날짜를 {month, week_of_month, ...}
구조로 비교해야 한다. 상대 표현('다음 주')은 값 정규화하지 않는다(맥락 필요).
"""

from __future__ import annotations

import unicodedata

from .korean_numbers import SINO_DIGIT, SMALL_UNIT, parse_number

_WEEK = {"첫째": 1, "둘째": 2, "셋째": 3, "넷째": 4, "다섯째": 5}
_PART = ("초", "중순", "말")
_NUMCHARS = set("0123456789,") | set(SINO_DIGIT) | set(SMALL_UNIT)


def _int_before(s: str, marker: str) -> int | None:
    """marker(월/일/분 등) 바로 앞의 숫자 표현을 정수로."""
    idx = s.find(marker)
    if idx < 0:
        return None
    k = idx - 1
    chars = []
    while k >= 0 and s[k] in _NUMCHARS:
        chars.append(s[k])
        k -= 1
    if not chars:
        return None
    r = parse_number("".join(reversed(chars)))
    return int(r.value) if r.kind == "value" and r.value is not None else None


def parse_date(text: str) -> dict | None:
    """날짜를 {month, day?, week_of_month?, part?}로. 못 잡으면 None."""
    s = unicodedata.normalize("NFC", text).strip()
    out: dict = {}

    month = _int_before(s, "월")
    if month is not None:
        out["month"] = month

    day = _int_before(s, "일")
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


def parse_time(text: str) -> dict | None:
    """시각을 {hour?, minute?}로. 못 잡으면 None."""
    s = unicodedata.normalize("NFC", text).strip()
    out: dict = {}

    if "시" in s:
        left = s[: s.find("시")].strip()
        tok = left.split()[-1] if left.split() else ""
        r = parse_number(tok)
        if r.kind == "value" and r.value is not None:
            out["hour"] = int(r.value)
        else:
            hour = _int_before(s, "시")
            if hour is not None:
                out["hour"] = hour

    minute = _int_before(s, "분")
    if minute is not None:
        out["minute"] = minute
    elif "반" in s and "hour" in out:
        out["minute"] = 30

    return out or None

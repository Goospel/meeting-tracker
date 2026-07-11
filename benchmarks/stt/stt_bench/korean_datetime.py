"""날짜/시각 파서 — 구조화된 값 비교.

'8월 출시 확정'이 '9월'로 번복되는 것을 잡으려면 날짜를 {month, week_of_month, ...}
구조로 비교해야 한다. 상대 표현('다음 주')은 값 정규화하지 않는다(맥락 필요).

핵심 메커니즘(코드리뷰 R6/R7/R2/S2): 마커(월/일/시/분)를 표면 첫/마지막 매치로
잡지 않고, **모든 출현을 우편향 순회하며 '파싱 가능한 숫자 좌맥락(공백 스킵)이 있고
지속시간('간')이 아닌' 출현**을 고르는 단일 탐색기 `_value_before_marker`로 통일한다.
part('초/말')는 월/일 맥락이 설 때만 인정한다(R8).
"""

from __future__ import annotations

import re
import unicodedata

from .korean_numbers import NUM_CORE, parse_number

_WEEK = {"첫째": 1, "둘째": 2, "셋째": 3, "넷째": 4, "다섯째": 5, "첫": 1}
_PART = ("초", "중순", "말")
_IRREGULAR_MONTH = {"유월": 6, "시월": 10}
_DURATION_NEXT = {"간"}   # 시간/분간/일간 등 지속시간
# 요일은 어휘로 소비해 월/일 마커 오프셋에서 제외 (R7: '월요일'의 월/일 오독 방지).
_WEEKDAY_RE = re.compile(r"[월화수목금토일]요일")


def _number_left(s: str, idx: int) -> int | None:
    """s[idx](마커) 바로 앞의 숫자를 정수로. 숫자-마커 사이 공백은 건너뛴다(R2).

    공백 너머에 숫자가 없으면 None — 비숫자 단어에 앵커되지 않는다.
    """
    k = idx
    while k > 0 and s[k - 1] == " ":
        k -= 1
    j = k
    while j > 0 and s[j - 1] in NUM_CORE:
        j -= 1
    token = s[j:k].strip()
    if not token:
        return None
    r = parse_number(token)
    return int(r.value) if r.kind == "value" and r.value is not None else None


def _value_before_marker(s: str, marker: str) -> tuple[int | None, int]:
    """marker 출현 중 '숫자 좌맥락 있음 + 지속시간 아님'을 우편향으로 선택.

    우편향(오른쪽부터)인 이유: 숫자 자체가 마커 문자를 포함할 수 있어('이십일일'의
    일=1) 마지막 유효 출현이 의도된 값이다. 반환 (value|None, marker_index).
    """
    occurrences = [i for i, ch in enumerate(s) if ch == marker]
    for i in reversed(occurrences):
        if i + 1 < len(s) and s[i + 1] in _DURATION_NEXT:
            continue
        val = _number_left(s, i)
        if val is not None:
            return val, i
    return None, -1


def parse_date(text: str) -> dict | None:
    """날짜를 {month, day?, week_of_month?, part?}로. 못 잡으면 None."""
    s = unicodedata.normalize("NFC", text).strip()
    s = _WEEKDAY_RE.sub(" ", s)   # 요일 소비 (R7)
    out: dict = {}

    month, month_end = None, 0
    for word, val in _IRREGULAR_MONTH.items():
        i = s.find(word)
        if i >= 0:
            month, month_end = val, i + len(word)
            break
    if month is None:
        mv, mi = _value_before_marker(s, "월")
        if mv is not None:
            month, month_end = mv, mi + 1
    if month is not None:
        out["month"] = month

    # 월을 뗀 나머지에서 일(日) — 월 안의 '일'(=1)이나 요일에 앵커되지 않도록.
    rest = s[month_end:]
    dv, _ = _value_before_marker(rest, "일")
    if dv is not None:
        out["day"] = dv

    if "주" in s:
        for word, val in sorted(_WEEK.items(), key=lambda kv: -len(kv[0])):
            if word in s:
                out["week_of_month"] = val
                break

    # part는 월/일 맥락이 설 때만 (R8: '정말'·'초안'의 유령 part 방지)
    if "month" in out or "day" in out:
        for part in _PART:
            if part in s:
                out["part"] = part
                break

    return out or None


def parse_time(text: str) -> dict | None:
    """시각을 {hour?, minute?, meridiem?}로. 못 잡으면 None."""
    s = unicodedata.normalize("NFC", text).strip()
    out: dict = {}

    hour, _ = _value_before_marker(s, "시")
    if hour is not None:
        out["hour"] = hour

    minute, _ = _value_before_marker(s, "분")
    if minute is not None:
        out["minute"] = minute
    elif "반" in s and "hour" in out:
        out["minute"] = 30

    if "오후" in s:
        out["meridiem"] = "pm"
    elif "오전" in s:
        out["meridiem"] = "am"

    return out or None

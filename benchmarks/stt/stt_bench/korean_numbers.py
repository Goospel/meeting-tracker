"""한국어 수 파서 — 값 등가 비교의 토대.

STT가 '3천만원'을 '2천만원'으로 오인식하면 모순감지 입력이 오염된다. 이 파서는
표면형이 달라도(3천만 = 삼천만 = 30,000,000) 같은 값이면 같게, 값이 다르면 다르게
본다. 범위('이삼천만')·근사('약~정도')는 단일 값으로 뭉개지 않고 분리 반환해
'None → 삭제'로 조용히 흡수되는 편향을 막는다.

단위(원·번·%·개…)는 화이트리스트가 아니라 "수(數) 문자가 아닌 후행 런"으로 일반
분리하고, 고유어 합성 수사(열두=12, 스물셋=23)와 소수점(3.5)까지 값으로 읽는다
— 이 셋을 놓치면 정상 인식된 치명 토큰이 삭제로 오집계된다(코드리뷰 F4/F7/F9).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 한자어 숫자
SINO_DIGIT = {
    "영": 0, "공": 0, "일": 1, "이": 2, "삼": 3, "사": 4,
    "오": 5, "육": 6, "륙": 6, "칠": 7, "팔": 8, "구": 9,
}
SMALL_UNIT = {"십": 10, "백": 100, "천": 1000}
BIG_UNIT = {"만": 10**4, "억": 10**8, "조": 10**12, "경": 10**16}

# 고유어 수사 — 단일 토큰(단독 사용형)
NATIVE = {
    "하나": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3, "넷": 4, "네": 4,
    "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
    "스물": 20, "스무": 20, "서른": 30, "마흔": 40, "쉰": 50,
    "예순": 60, "일흔": 70, "여든": 80, "아흔": 90,
}
# 고유어 합성용 — 십의 자리 + 일의 자리 (열두=열+두, 스물셋=스물+셋)
NATIVE_TENS = {"열": 10, "스물": 20, "스무": 20, "서른": 30, "마흔": 40,
               "쉰": 50, "예순": 60, "일흔": 70, "여든": 80, "아흔": 90}
NATIVE_ONES = {"하나": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3,
               "넷": 4, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9}
_TENS_KEYS = sorted(NATIVE_TENS, key=len, reverse=True)

# 수(數) 문자 집합 — 이 밖의 후행 런은 단위로 분리한다.
_NATIVE_CHARS = set("".join(list(NATIVE) + list(NATIVE_TENS) + list(NATIVE_ONES)))
NUM_CORE = set("0123456789.,") | set(SINO_DIGIT) | set(SMALL_UNIT) | set(BIG_UNIT) | _NATIVE_CHARS

# 근사·헤지 표지 ('한'은 고유어 1과 충돌하므로 제외)
_HEDGE = ("약", "대략", "얼추", "거의", "정도", "쯤", "가량", "남짓")

_RANGE_SEPS = ("~", "∼", "〜", "–", "—")
_DEC_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class NumberParse:
    kind: str                 # 'value' | 'range' | 'ambiguous' | 'none'
    value: float | None = None
    low: int | None = None
    high: int | None = None
    unit: str | None = None
    hedge: bool = False
    reason: str = ""


def _parse_native(core: str) -> int | None:
    """고유어 수사(단일 또는 십+일 합성)를 정수로. 아니면 None."""
    if core in NATIVE:
        return NATIVE[core]
    for tens in _TENS_KEYS:
        if core.startswith(tens) and len(core) > len(tens):
            ones = core[len(tens):]
            if ones in NATIVE_ONES:
                return NATIVE_TENS[tens] + NATIVE_ONES[ones]
    return None


def _parse_sino_arabic(s: str) -> int | None:
    """한자어/아라비아 혼합 수를 정수로. 파싱 불가 시 None."""
    total = section = num = 0
    seen = False
    for ch in s:
        if ch.isdigit():
            num = num * 10 + int(ch)
            seen = True
        elif ch in SINO_DIGIT:
            num = num * 10 + SINO_DIGIT[ch]
            seen = True
        elif ch in SMALL_UNIT:
            section += (num or 1) * SMALL_UNIT[ch]
            num = 0
            seen = True
        elif ch in BIG_UNIT:
            chunk = section + num
            total += (chunk or 1) * BIG_UNIT[ch]
            section = num = 0
            seen = True
        else:
            return None
    return total + section + num if seen else None


def _detect_range(core: str) -> tuple[int, int] | None:
    """범위 표현이면 (low, high), 아니면 None."""
    for sep in _RANGE_SEPS:
        if sep in core:
            a, _, b = core.partition(sep)
            va, vb = _parse_sino_arabic(a), _parse_sino_arabic(b)
            if va is not None and vb is not None:
                return (min(va, vb), max(va, vb))
            return None
    # 선행 연속 한자어 숫자쌍이 뒤를 공유 (이삼천만 = 2천만~3천만)
    if len(core) >= 2 and core[0] in SINO_DIGIT and core[1] in SINO_DIGIT:
        d0, d1 = SINO_DIGIT[core[0]], SINO_DIGIT[core[1]]
        if d1 == d0 + 1:
            rest = core[2:]
            lo = _parse_sino_arabic(core[0] + rest)
            hi = _parse_sino_arabic(core[1] + rest)
            if lo is not None and hi is not None and lo != hi:
                return (min(lo, hi), max(lo, hi))
    return None


def parse_number(text: str) -> NumberParse:
    """한국어 수 표현을 파싱. 반환 kind: value/range/ambiguous/none."""
    s = unicodedata.normalize("NFC", text).strip()
    if not s:
        return NumberParse("none", reason="empty")

    hedge = False
    for h in _HEDGE:
        if h in s:
            hedge = True
            s = s.replace(h, " ")
    s = s.rstrip()

    # 후행 단위 = 수 문자가 아닌 마지막 런(사이 공백 허용). 화이트리스트 불필요.
    k = len(s)
    while k > 0 and s[k - 1] not in NUM_CORE:
        k -= 1
    unit = s[k:].strip() or None
    core = s[:k].replace(",", "").replace(" ", "")
    if not core:
        return NumberParse("none", unit=unit, hedge=hedge, reason="no-number")

    if _DEC_RE.fullmatch(core):
        value = float(core) if "." in core else int(core)
        return NumberParse("value", value=value, unit=unit, hedge=hedge)

    rng = _detect_range(core)
    if rng is not None:
        return NumberParse("range", low=rng[0], high=rng[1], unit=unit, hedge=hedge)

    nv = _parse_native(core)
    if nv is not None:
        return NumberParse("value", value=nv, unit=unit, hedge=hedge)

    val = _parse_sino_arabic(core)
    if val is None:
        return NumberParse("none", unit=unit, hedge=hedge, reason="unparseable")
    return NumberParse("value", value=val, unit=unit, hedge=hedge)

"""한국어 수 파서 — 값 등가 비교의 토대.

STT가 '3천만원'을 '2천만원'으로 오인식하면 모순감지 입력이 오염된다. 이 파서는
표면형이 달라도(3천만 = 삼천만 = 30,000,000) 같은 값이면 같게, 값이 다르면 다르게
본다.

수/단위 경계는 문자 집합이 아니라 **문법 규칙**으로 가른다(코드리뷰 R1):
  - 아라비아 숫자 런 직후의 SINO_DIGIT('3일'의 일)은 자릿수 연결이 아니라 단위 경계.
  - 고유어 수사는 토큰 사전 최장일치로 core를 확정하고 나머지를 단위로.
범위('2~3천만', '이천만에서 삼천만')는 단일 값으로 뭉개지 않고 분리 반환한다(R9/R11).
전각 숫자(R1/S5)·소수+큰단위·한글 소수(S6)까지 값으로 읽는다.
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
_PLACE = {**SMALL_UNIT, **BIG_UNIT}

# 고유어 수사 — 단일 토큰(단독 사용형)
NATIVE = {
    "하나": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3, "넷": 4, "네": 4,
    "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
    "스물": 20, "스무": 20, "서른": 30, "마흔": 40, "쉰": 50,
    "예순": 60, "일흔": 70, "여든": 80, "아흔": 90,
}
# 고유어 합성 — 십의 자리 + 일의 자리 (열두=열+두, 스물셋=스물+셋)
NATIVE_TENS = {"열": 10, "스물": 20, "스무": 20, "서른": 30, "마흔": 40,
               "쉰": 50, "예순": 60, "일흔": 70, "여든": 80, "아흔": 90}
NATIVE_ONES = {"하나": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3,
               "넷": 4, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9}
_TENS_KEYS = sorted(NATIVE_TENS, key=len, reverse=True)
_ONES_KEYS = sorted(NATIVE_ONES, key=len, reverse=True)
_NATIVE_KEYS = sorted(NATIVE, key=len, reverse=True)

# 수(數) 문자 집합 — datetime/score의 좌맥락 수집·salvage에서 공용.
_NATIVE_CHARS = set("".join(list(NATIVE) + list(NATIVE_TENS) + list(NATIVE_ONES)))
NUM_CORE = set("0123456789.,") | set(SINO_DIGIT) | set(SMALL_UNIT) | set(BIG_UNIT) | _NATIVE_CHARS

# 단위 분류 — 채점기의 단위-타입 정합성 판정에 쓴다(R4/R5).
CURRENCY_UNITS = {"원": "KRW", "₩": "KRW", "달러": "USD", "불": "USD", "$": "USD",
                  "유로": "EUR", "엔": "JPY", "위안": "CNY"}
PCT_UNITS = {"%", "퍼센트", "프로", "％"}
_TIME_UNITS = {"시", "분", "초"}
_DATE_UNITS = {"일", "월", "년", "주", "개월", "주일"}

# 근사·헤지 표지 ('한'은 고유어 1과 충돌하므로 제외)
_HEDGE = ("약", "대략", "얼추", "거의", "정도", "쯤", "가량", "남짓")

# 범위 구분자 — 기호 + 구어(에서/부터/내지). '에서'는 조사 동형이라 양쪽이 수로
# 파싱될 때만 범위로 인정한다(R11).
_RANGE_SEPS = ("에서", "부터", "내지", "~", "∼", "〜", "–", "—", "-")

_FULLWIDTH = str.maketrans("０１２３４５６７８９％，．", "0123456789%,.")
_DEC_RE = re.compile(r"\d+(?:\.\d+)?")
_DEC_UNIT_RE = re.compile(r"(\d+\.\d+)([십백천만억조경]+)")


@dataclass
class NumberParse:
    kind: str                 # 'value' | 'range' | 'ambiguous' | 'none'
    value: float | None = None
    low: int | None = None
    high: int | None = None
    unit: str | None = None
    hedge: bool = False
    reason: str = ""


def currency_code(unit: str | None) -> str | None:
    return CURRENCY_UNITS.get(unit) if unit else None


def unit_category(unit: str | None) -> str | None:
    if not unit:
        return None
    if unit in CURRENCY_UNITS:
        return "currency"
    if unit in PCT_UNITS:
        return "percent"
    if unit in _TIME_UNITS:
        return "time"
    if unit in _DATE_UNITS:
        return "date"
    return "count"


# ── 수 core / 단위 분리 (문법 경계) ─────────────────────────────────────
def _native_prefix_len(s: str) -> int:
    for tens in _TENS_KEYS:
        if s.startswith(tens):
            rest = s[len(tens):]
            for ones in _ONES_KEYS:
                if rest.startswith(ones):
                    return len(tens) + len(ones)
            return len(tens)
    for tok in _NATIVE_KEYS:
        if s.startswith(tok):
            return len(tok)
    return 0


def _sino_prefix_len(s: str) -> int:
    i, saw_arabic = 0, False
    while i < len(s):
        ch = s[i]
        if ch.isdigit():
            saw_arabic = True
            i += 1
        elif ch in ".,점":
            i += 1
        elif ch in _PLACE:
            i += 1
            saw_arabic = False
        elif ch in SINO_DIGIT and not saw_arabic:
            i += 1
        else:
            break
    return i


def _split_number_unit(s: str) -> tuple[str, str]:
    """수 core와 후행 단위를 문법 경계로 분리."""
    nlen = _native_prefix_len(s)
    if nlen:
        return s[:nlen], s[nlen:].strip()
    slen = _sino_prefix_len(s)
    return s[:slen], s[slen:].strip()


# ── 수치 파싱 ───────────────────────────────────────────────────────────
def _parse_native(core: str) -> int | None:
    if core in NATIVE:
        return NATIVE[core]
    for tens in _TENS_KEYS:
        if core.startswith(tens) and len(core) > len(tens):
            ones = core[len(tens):]
            if ones in NATIVE_ONES:
                return NATIVE_TENS[tens] + NATIVE_ONES[ones]
    return None


def _parse_sino_arabic(s: str) -> int | None:
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


def _korean_decimal(core: str) -> float | None:
    """'삼점오' / '3점5' 형 한글 소수 (S6)."""
    if "점" not in core:
        return None
    a, _, b = core.partition("점")
    va = 0 if a == "" else _parse_sino_arabic(a)
    if va is None or not b:
        return None
    frac = []
    for ch in b:
        if ch.isdigit():
            frac.append(ch)
        elif ch in SINO_DIGIT:
            frac.append(str(SINO_DIGIT[ch]))
        else:
            return None
    return va + int("".join(frac)) / (10 ** len(frac))


# ── 범위 ────────────────────────────────────────────────────────────────
def _has_place(x: str) -> bool:
    return any(c in _PLACE for c in x)


def _trailing_place(b: str) -> str:
    """앞자리 숫자를 뗀 나머지(공유 접미). '3천만' -> '천만'."""
    i = 0
    while i < len(b) and (b[i].isdigit() or b[i] in SINO_DIGIT):
        i += 1
    return b[i:]


def _range_parts(a: str, b: str) -> tuple[int, int] | None:
    ra, rb = parse_number(a), parse_number(b)
    if ra.kind != "value" or rb.kind != "value" or ra.value is None or rb.value is None:
        return None
    lo, hi = ra.value, rb.value
    # 공유 접미 분배: a가 자릿수 단위 없이 끝나고 b가 더 큰 스케일 (2~3천만)
    if lo < hi and not _has_place(a) and _has_place(b):
        suffix = _trailing_place(b)
        if suffix:
            ra2 = parse_number(a + suffix)
            if ra2.kind == "value" and ra2.value is not None:
                lo = ra2.value
    return (int(min(lo, hi)), int(max(lo, hi)))


def _detect_sep_range(s: str) -> tuple[int, int] | None:
    for sep in _RANGE_SEPS:
        if sep in s:
            a, _, b = s.partition(sep)
            return _range_parts(a.strip(), b.strip())
    return None


def _detect_pair_range(core: str) -> tuple[int, int] | None:
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
    s = unicodedata.normalize("NFC", text).translate(_FULLWIDTH).strip()
    if not s:
        return NumberParse("none", reason="empty")

    hedge = False
    for h in _HEDGE:
        if h in s:
            hedge = True
            s = s.replace(h, " ")
    s = s.strip()

    rng = _detect_sep_range(s)
    if rng is not None:
        return NumberParse("range", low=rng[0], high=rng[1], hedge=hedge)

    number, unit = _split_number_unit(s)
    unit = unit or None
    number = number.replace(",", "").replace(" ", "")
    if not number:
        return NumberParse("none", unit=unit, hedge=hedge, reason="no-number")

    rng2 = _detect_pair_range(number)
    if rng2 is not None:
        return NumberParse("range", low=rng2[0], high=rng2[1], unit=unit, hedge=hedge)

    kd = _korean_decimal(number)
    if kd is not None:
        return NumberParse("value", value=kd, unit=unit, hedge=hedge)

    if _DEC_RE.fullmatch(number):
        value = float(number) if "." in number else int(number)
        return NumberParse("value", value=value, unit=unit, hedge=hedge)

    if "." in number:
        m = _DEC_UNIT_RE.fullmatch(number)
        if m:
            mult = _parse_sino_arabic(m.group(2))
            if mult is not None:
                return NumberParse("value", value=float(m.group(1)) * mult, unit=unit, hedge=hedge)

    nv = _parse_native(number)
    if nv is not None:
        return NumberParse("value", value=nv, unit=unit, hedge=hedge)

    val = _parse_sino_arabic(number)
    if val is None:
        return NumberParse("none", unit=unit, hedge=hedge, reason="unparseable")
    return NumberParse("value", value=val, unit=unit, hedge=hedge)

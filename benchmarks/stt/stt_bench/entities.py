"""치명 토큰(critical entity) 데이터 모델.

모순감지에 치명적인 토큰만 골든 레퍼런스에서 '사람이 수동 주석'한다. 추출기를
오염된 hypothesis에 돌리지 않는 것이 핵심 — 추출기 recall이 sub/del 판정을
좌우하면 KPI 신뢰가 무너지기 때문(방법론 스펙 high 결함).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EntityType(str, Enum):
    AMOUNT = "AMOUNT"                # 금액
    NUMBER = "NUMBER"               # 순수 수량
    DATE = "DATE"                   # 날짜
    TIME = "TIME"                   # 시각
    PERCENT = "PERCENT"             # 비율
    UNIT_QUANTITY = "UNIT_QUANTITY"  # 단위 결합 수량 (세 편, 3층)
    PROPER_NOUN = "PROPER_NOUN"     # 고유명사 (인명·제품·조직)
    RANGE = "RANGE"                 # 범위 (이삼천만)
    POLARITY = "POLARITY"           # 극성/확정·부정 신호어 (v2)


@dataclass
class CriticalEntity:
    entity_id: str
    type: EntityType
    char_start: int          # segment.text(NFC)의 문자 오프셋 (inclusive)
    char_end: int            # (exclusive) — text[char_start:char_end] == surface
    surface: str
    canonical: dict          # 값 등가 비교 기준 (AMOUNT: {value,unit}, DATE: {month,...})
    criticality: str = "high"
    contradiction_key: str | None = None   # 같은 의미축 공유 (budget_cap 등) — v2 역할스왑용
    aliases: tuple = ()                      # PROPER_NOUN 축약 허용목록 (인스타=인스타그램)
    flags: dict = field(default_factory=dict)  # hedge/range/ambiguous/semantic_flip
    speaker: str | None = None               # v2 화자귀속 지표용


@dataclass
class Segment:
    segment_id: str
    speaker: str
    start_sec: float
    end_sec: float
    text: str
    critical_entities: list = field(default_factory=list)

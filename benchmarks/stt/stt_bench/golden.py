"""골든셋/hypothesis 로더 + 검증 게이트.

검증 게이트(커밋 훅에서 강제할 불변식): text는 NFC, text[cs:ce]==surface,
canonical 비어있지 않음. 오프셋이 어긋난 주석은 채점을 조용히 오염시키므로
로드 시점에 막는다.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

from .entities import CriticalEntity, EntityType, Segment
from .korean_datetime import parse_date, parse_time


def load_golden(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    segments = []
    for s in data["segments"]:
        ents = [
            CriticalEntity(
                entity_id=e["entity_id"],
                type=EntityType(e["type"]),
                char_start=e["char_start"],
                char_end=e["char_end"],
                surface=e["surface"],
                canonical=e["canonical"],
                criticality=e.get("criticality", "high"),
                contradiction_key=e.get("contradiction_key"),
                aliases=tuple(e.get("aliases", ())),
                flags=e.get("flags", {}),
                speaker=e.get("speaker"),
            )
            for e in s.get("critical_entities", [])
        ]
        segments.append(
            Segment(
                segment_id=s["segment_id"],
                speaker=s["speaker"],
                start_sec=s["start_sec"],
                end_sec=s["end_sec"],
                text=s["text"],
                critical_entities=ents,
            )
        )
    return {
        "clip_id": data["clip_id"],
        "audio": data.get("audio", {}),
        "segments": segments,
        "raw": data,
    }


def validate_golden(golden: dict) -> bool:
    """불변식 위반 시 ValueError. 통과하면 True."""
    for seg in golden["segments"]:
        if unicodedata.normalize("NFC", seg.text) != seg.text:
            raise ValueError(f"segment {seg.segment_id}: text가 NFC가 아님")
        for e in seg.critical_entities:
            got = seg.text[e.char_start : e.char_end]
            if got != e.surface:
                raise ValueError(
                    f"{e.entity_id}: 오프셋 불일치 "
                    f"text[{e.char_start}:{e.char_end}]={got!r} != surface {e.surface!r}"
                )
            if not e.canonical:
                raise ValueError(f"{e.entity_id}: canonical 비어있음")
            # DATE/TIME 과소명세 차단 (F10): surface가 담은 필드를 canonical이 다 pin해야 한다.
            # (예: surface '8월 15일'인데 canonical {month:8}만이면 day 반전이 은폐됨)
            if e.type in (EntityType.DATE, EntityType.TIME):
                parsed = (parse_date if e.type == EntityType.DATE else parse_time)(e.surface)
                if not parsed:
                    raise ValueError(f"{e.entity_id}: {e.type.value} surface {e.surface!r} 파싱 실패")
                for k, v in parsed.items():
                    if e.canonical.get(k) != v:
                        raise ValueError(
                            f"{e.entity_id}: {e.type.value} canonical이 surface 필드 "
                            f"{k}={v!r}를 누락/불일치 (canonical={e.canonical})"
                        )
    return True


def load_hypothesis(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "clip_id": data["clip_id"],
        "provider": data.get("provider", "?"),
        "segments": data["segments"],
    }

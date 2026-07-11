"""골든셋/hypothesis 로더 + 검증 게이트.

검증 게이트(커밋 훅에서 강제할 불변식): text는 NFC, text[cs:ce]==surface, canonical
비어있지 않음, segment_id 유일. 더해 **parse(surface) ≡ canonical 완전 동치**를
강제한다(R12) — 값 오타·여분 키·파싱 불가 surface가 조용히 통과해 채점 불능이 되는
순환을 막는다. 파서 밖 정당 표기('정오' 등)는 엔티티 flags.manual=true로 opt-out.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

from .entities import CriticalEntity, EntityType, Segment
from .korean_datetime import parse_date, parse_time
from .korean_numbers import currency_code, parse_number

_NUMERIC = (EntityType.AMOUNT, EntityType.NUMBER, EntityType.PERCENT, EntityType.UNIT_QUANTITY)


def load_golden(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))  # BOM 허용 (S1)
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


def _check_canonical(e: CriticalEntity) -> None:
    """parse(surface) ≡ canonical 완전 동치 강제 (R12). manual은 면제."""
    if e.flags.get("manual"):
        return
    t = e.type

    if t in (EntityType.DATE, EntityType.TIME):
        parsed = (parse_date if t == EntityType.DATE else parse_time)(e.surface)
        if not parsed:
            raise ValueError(f"{e.entity_id}: {t.value} surface {e.surface!r} 파싱 실패 (flags.manual로 opt-out 가능)")
        if parsed != e.canonical:
            raise ValueError(f"{e.entity_id}: {t.value} canonical {e.canonical} != parse(surface) {parsed}")
        return

    if t in _NUMERIC:
        r = parse_number(e.surface)
        if r.kind != "value":
            raise ValueError(f"{e.entity_id}: {t.value} surface {e.surface!r} 값 파싱 실패 ({r.kind})")
        if r.value != e.canonical.get("value"):
            raise ValueError(f"{e.entity_id}: value {e.canonical.get('value')} != parse(surface) {r.value}")
        if t == EntityType.UNIT_QUANTITY:
            gu = e.canonical.get("unit")
            if gu is not None and r.unit != gu:
                raise ValueError(f"{e.entity_id}: unit {gu!r} != parse(surface) {r.unit!r}")
        elif t == EntityType.AMOUNT:
            gc, hc = e.canonical.get("unit"), currency_code(r.unit)
            if gc and hc and hc != gc:
                raise ValueError(f"{e.entity_id}: 통화 {gc} != parse(surface) {hc}")
        return

    if t == EntityType.RANGE:
        r = parse_number(e.surface)
        if r.kind != "range":
            raise ValueError(f"{e.entity_id}: RANGE surface {e.surface!r} 범위 파싱 실패 ({r.kind})")
        if r.low != e.canonical.get("low") or r.high != e.canonical.get("high"):
            raise ValueError(f"{e.entity_id}: RANGE canonical != parse(surface) ({r.low},{r.high})")


def validate_golden(golden: dict) -> bool:
    """불변식 위반 시 ValueError. 통과하면 True."""
    seen_ids: set[str] = set()
    for seg in golden["segments"]:
        if seg.segment_id in seen_ids:                # 중복 id (S9)
            raise ValueError(f"segment_id 중복: {seg.segment_id!r}")
        seen_ids.add(seg.segment_id)

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
            _check_canonical(e)
    return True


def load_hypothesis(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))  # BOM 허용 (S1)
    return {
        "clip_id": data["clip_id"],
        "provider": data.get("provider", "?"),
        "segments": data["segments"],
    }

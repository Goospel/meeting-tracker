"""골든 라벨/예측 데이터 모델 + 로더 + 검증 게이트.

flag = 회의 흐름단절 1건(모순/번복/미해결/재논의). statements는 상충/근거 발언(보통 2개,
미해결은 1개). 골든은 전사 세그먼트와 **양방향 일관**해야 한다:
  - 세그먼트가 flag을 역참조(`transcript[].flags`)하고,
  - flag statement의 인용이 그 세그먼트에 grounding된다.
이 일관성이 stage-2의 무드리프트 방지 게이트다(stage-1 오프셋 불변식에 대응).
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .grounding import resolve_flag_segments


class FlagType(str, Enum):
    CONTRADICTION = "모순"      # 같은 사람이 앞뒤로 다른 말
    REVERSAL = "번복"           # 확정했던 결정이 조용히 뒤집힘
    UNRESOLVED = "미해결"       # 꺼내놓고 다시 안 다룬 안건
    REDISCUSSION = "재논의"     # 이견이 결론 없이 넘어감


@dataclass
class Statement:
    speaker: str
    quote: str
    time_sec: float | None = None


@dataclass
class FlowFlag:
    flag_id: str
    type: FlagType
    statements: list
    severity: str = "medium"
    title: str = ""
    topic: str = ""
    explanation: str = ""
    resolution: str = ""


@dataclass
class TranscriptSegment:
    segment_id: str
    speaker: str
    text: str
    start_sec: float = 0.0
    flags: tuple = ()                    # 골든 역참조(flag id들)


def _statement_from_data(d: dict) -> Statement:
    return Statement(
        speaker=d.get("speaker", ""),
        quote=unicodedata.normalize("NFC", d.get("quote", "")),
        time_sec=d.get("time_sec"),
    )


def flag_from_data(d: dict) -> FlowFlag:
    return FlowFlag(
        flag_id=d.get("flag_id") or d["id"],          # 골든/예측은 "id", 내부는 flag_id
        type=FlagType(d["type"]),                     # 미지 유형은 여기서 ValueError
        statements=[_statement_from_data(s) for s in d.get("statements", [])],
        severity=d.get("severity", "medium"),
        title=d.get("title", ""),
        topic=d.get("topic", ""),
        explanation=d.get("explanation", ""),
        resolution=d.get("resolution", ""),
    )


def _segment_from_data(d: dict) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id=d.get("segment_id") or d["id"],
        speaker=d.get("speaker", ""),
        text=unicodedata.normalize("NFC", d.get("text", "")),
        start_sec=d.get("start_sec", 0.0),
        flags=tuple(d.get("flags", ())),
    )


def meeting_from_data(data: dict) -> dict:
    """골든 raw dict → {meta, transcript, flags, raw}. 파일 경유 없이 파싱 재사용."""
    return {
        "meta": data.get("meta", {}),
        "transcript": [_segment_from_data(s) for s in data.get("transcript", [])],
        "flags": [flag_from_data(f) for f in data.get("flags", [])],
        "raw": data,
    }


def load_meeting(path: str | Path) -> dict:
    return meeting_from_data(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def load_pred_flags(path: str | Path) -> list:
    """예측 flag 로더 — 리스트 또는 {"flags":[...]} 둘 다 허용(Claude 출력 형태 유연)."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    items = data["flags"] if isinstance(data, dict) else data
    return [flag_from_data(f) for f in items]


def validate_golden(meeting: dict) -> bool:
    """골든 불변식 위반 시 ValueError. 통과하면 True.

    게이트(무드리프트 방지 — stage-1 오프셋 불변식에 대응):
      - segment_id 유일 · flag id 유일 · statements 비어있지 않음(speaker/quote 존재)
      - **정방향**: 각 flag 인용이 전사에 grounding되고, 그 세그먼트가 그 flag을 역참조
      - **역방향**: 세그먼트가 역참조하는 flag은 존재하고, 그 flag이 이 세그먼트에 grounding
        (orphan back-ref 차단)
    """
    transcript = meeting["transcript"]
    flags = meeting["flags"]

    seg_ids = set()
    for s in transcript:
        if s.segment_id in seg_ids:
            raise ValueError(f"segment_id 중복: {s.segment_id!r}")
        seg_ids.add(s.segment_id)

    flag_segset: dict = {}
    for f in flags:
        if f.flag_id in flag_segset:
            raise ValueError(f"flag id 중복: {f.flag_id!r}")
        if not f.statements:
            raise ValueError(f"{f.flag_id}: statements가 비어있음")
        for st in f.statements:
            if not st.speaker or not st.quote:
                raise ValueError(f"{f.flag_id}: statement에 speaker/quote 누락")
        segs, ungrounded = resolve_flag_segments(f, transcript)
        if ungrounded:
            raise ValueError(
                f"{f.flag_id}: 인용이 전사본에 grounding되지 않음 (malformed 골든): {ungrounded[0]!r}"
            )
        flag_segset[f.flag_id] = segs

    seg_by_id = {s.segment_id: s for s in transcript}
    # 정방향 — grounding된 세그먼트가 그 flag을 역참조.
    for fid, segs in flag_segset.items():
        for sid in segs:
            if fid not in seg_by_id[sid].flags:
                raise ValueError(
                    f"{fid}: grounding된 세그먼트 {sid}가 이 flag을 역참조하지 않음 "
                    f"(seg.flags={seg_by_id[sid].flags})"
                )
    # 역방향 — 세그먼트가 역참조하는 flag은 존재하고 이 세그먼트에 grounding(orphan 차단).
    for s in transcript:
        for fid in s.flags:
            if fid not in flag_segset:
                raise ValueError(f"세그먼트 {s.segment_id}가 없는 flag {fid!r}를 역참조")
            if s.segment_id not in flag_segset[fid]:
                raise ValueError(
                    f"orphan 역참조: 세그먼트 {s.segment_id}가 flag {fid!r}를 역참조하나 "
                    f"그 flag이 이 세그먼트에 grounding되지 않음"
                )
    return True

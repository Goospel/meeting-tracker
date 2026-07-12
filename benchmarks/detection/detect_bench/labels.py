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


# 보수적 별칭표 — 실제 Claude가 영문 라벨을 낼 때만 정규화(자의적 한글 유의어는 넣지 않는다).
_TYPE_ALIASES = {
    "contradiction": FlagType.CONTRADICTION,
    "reversal": FlagType.REVERSAL,
    "unresolved": FlagType.UNRESOLVED,
    "rediscussion": FlagType.REDISCUSSION,
}


def _coerce_type(raw, *, strict: bool):
    """type 라벨 → FlagType. NFC 정규화 후 정식 유형 매칭, 실패 시 골든=에러/예측=강등.

    - **정규화 우선**: quote/text와 동일하게 NFC+strip 후 매칭 → NFD 분해형·공백 패딩된
      정식 한글 유형(정당한 골든)이 오거부되지 않는다.
    - **별칭은 예측 전용**: 영문 라벨 정규화(contradiction→모순 등)는 신뢰 불가 Claude
      출력에만 적용. 골든에 영문 라벨이 오면 malformed로 거부(엄격성 유지).
    - **예측 강등**: 정식도 별칭도 아니면 원문 str로 강등 — flag 하나의 변형 라벨이 run
      전체를 중단시키지 않게. 채점기가 미매칭 예측(가짜)으로 다룬다.
    """
    norm = unicodedata.normalize("NFC", str(raw)).strip()
    try:
        return FlagType(norm)                         # 정규화 후 정식 유형
    except ValueError:
        pass
    if strict:                                        # 골든: 별칭·강등 없이 엄격
        raise ValueError(
            f"미지의 흐름단절 유형: {raw!r} (허용: {[t.value for t in FlagType]})"
        )
    return _TYPE_ALIASES.get(norm.lower(), norm)      # 예측: 영문 별칭 정규화 또는 원문 강등


@dataclass
class Statement:
    speaker: str
    quote: str
    time_sec: float | None = None


@dataclass
class FlowFlag:
    flag_id: str
    type: "FlagType | str"          # 골든은 항상 FlagType, 예측 미지 라벨은 원문 str로 강등
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
    q = d.get("quote")
    sp = d.get("speaker")
    return Statement(                                 # quote/speaker 비문자열(예측 null/숫자)은 ""로 강등
        speaker=sp if isinstance(sp, str) else "",    # — normalize(None) TypeError로 배치가 죽지 않게
        quote=unicodedata.normalize("NFC", q) if isinstance(q, str) else "",
        time_sec=d.get("time_sec"),
    )


def _statements_from_data(raw, *, strict: bool) -> list:
    """statements 컨테이너/원소 shape 강등 — 신뢰 불가 예측이 배치를 죽이지 않게.

    골든은 엄격(비-list·비-dict 원소는 malformed로 raise). 예측은 강등(비-list→빈 목록,
    비-dict 원소→건너뜀). 키 부재/null 둘 다 빈 목록으로(골든이면 이후 validate_golden의
    '비어있음' 게이트가 거부).
    """
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        if strict:
            raise ValueError(f"statements가 리스트가 아님 (malformed 골든): {type(raw).__name__}")
        raw = []
    out = []
    for s in raw:
        if isinstance(s, dict):
            out.append(_statement_from_data(s))
        elif strict:
            raise ValueError(f"statement가 dict가 아님 (malformed 골든): {type(s).__name__}")
    return out


def flag_from_data(d: dict, *, strict: bool = True, fallback_id: str | None = None) -> FlowFlag:
    if not isinstance(d, dict):                       # 비-dict flag(골든=malformed, 예측=로더가 선필터)
        raise ValueError(f"flag이 dict가 아님 (malformed 골든): {type(d).__name__}")
    fid = d.get("flag_id") or d.get("id")             # 골든/예측은 "id", 내부는 flag_id
    if not fid:                                        # id 누락 — 골든은 malformed, 예측은 표시용 강등
        if strict:
            raise ValueError("flag에 id가 없음 (malformed 골든)")
        fid = fallback_id if fallback_id is not None else ""
    return FlowFlag(
        flag_id=fid,
        type=_coerce_type(d.get("type"), strict=strict),  # 키 누락(None)도 강등 경로로(예측 배치 안 죽게)
        statements=_statements_from_data(d.get("statements"), strict=strict),
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
    items = data.get("flags") if isinstance(data, dict) else data   # 키 부재도 None→클린 에러(subscript X)
    if not isinstance(items, list):                   # 구조적 오류(flags 부재/null/비-list)는 클린 에러
        raise ValueError("예측이 리스트 또는 {flags:[...]} 형태가 아님")
    # 예측은 변형 라벨/누락 키 per-flag 강등(배치 안 죽게). 비-dict flag는 건너뛰고, id 없으면 표시용 합성 id.
    return [flag_from_data(f, strict=False, fallback_id=f"pred{i}")
            for i, f in enumerate(items) if isinstance(f, dict)]


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

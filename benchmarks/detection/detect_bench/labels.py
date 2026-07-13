"""골든 라벨/예측 데이터 모델 + 로더 + 검증 게이트.

flag = 회의 흐름단절 1건(모순/번복/미해결/재논의). statements는 상충/근거 발언(보통 2개,
미해결은 1개). 골든은 전사 세그먼트와 **양방향 일관**해야 한다:
  - 세그먼트가 flag을 역참조(`transcript[].flags`)하고,
  - flag statement의 인용이 그 세그먼트에 grounding된다.
이 일관성이 stage-2의 무드리프트 방지 게이트다(stage-1 오프셋 불변식에 대응).
"""

from __future__ import annotations

import json
import math
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
    - **키 부재/null**: 원문이 없으므로 str(None)이 지어낸 "None"이 아니라 명시적 센티널 —
      실제로 "None"을 출력한 예측과 리포트에서 구분돼야 한다. 골든은 엄격 거부.
    """
    if raw is None:
        if strict:
            raise ValueError("flag에 type이 없음 (malformed 골든)")
        return "(type 누락)"
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


def _is_num(x) -> bool:
    """유한 숫자 판정(bool·NaN·±inf 제외) — 존재≠타입≠유한 3단 (T-029/T-030).

    [3R] `x == x`는 NaN만 걸러 ±Infinity가 통과했다(json.loads는 Infinity 리터럴 기본 허용) —
    start_sec=inf가 f"{...:.0f}"로 '[infs]'가 되어 프롬프트에 새고 힌트 산술도 무성 퇴화.
    isfinite로 독스트링의 '유한' 주장과 판정을 일치시킨다."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _statement_from_data(d: dict, *, strict: bool) -> Statement:
    q = d.get("quote")
    sp = d.get("speaker")
    ts = d.get("time_sec")
    if strict:                                        # 골든: 필드 레벨도 엄격 — 무성 강등은 증거 축소
        if not isinstance(q, str):
            raise ValueError(f"statement quote가 문자열이 아님 (malformed 골든): {type(q).__name__}")
        if not isinstance(sp, str):
            raise ValueError(f"statement speaker가 문자열이 아님 (malformed 골든): {type(sp).__name__}")
        if ts is not None and not _is_num(ts):
            raise ValueError(f"statement time_sec이 숫자가 아님 (malformed 골든): {ts!r}")
    return Statement(                                 # 예측: 비문자열(null/숫자)은 ""로 강등
        speaker=unicodedata.normalize("NFC", sp) if isinstance(sp, str) else "",   # 화자도 NFC —
        quote=unicodedata.normalize("NFC", q) if isinstance(q, str) else "",       # NFD 힌트 무성 불발 방지
        time_sec=ts,
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
            out.append(_statement_from_data(s, strict=strict))
        elif strict:
            raise ValueError(f"statement가 dict가 아님 (malformed 골든): {type(s).__name__}")
    return out


def _str_or(v, default: str, name: str, *, strict: bool) -> str:
    """메타 문자열 필드 강등 — 키 부재/null은 기본값, 비문자열은 골든=에러/예측=기본값.

    severity/title 등은 지금은 표시용이지만, 미래 소비자(리포트 열 추가 등)가 str 선언을 믿고
    None/숫자에 .upper() 등을 걸어 터지는 것을 경계에서 차단(quote/type 가드와 대칭)."""
    if v is None:
        return default
    if isinstance(v, str):
        return v
    if strict:
        raise ValueError(f"{name}이 문자열이 아님 (malformed 골든): {type(v).__name__}")
    return default


def flag_from_data(d: dict, *, strict: bool = True, fallback_id: str | None = None) -> FlowFlag:
    if not isinstance(d, dict):                       # 비-dict flag(골든=malformed, 예측=로더가 선필터)
        raise ValueError(f"flag이 dict가 아님 (malformed 골든): {type(d).__name__}")
    fid = d.get("flag_id")                            # 골든/예측은 "id", 내부는 flag_id
    if fid is None:
        fid = d.get("id")
    if fid is None:                                    # 존재 검사(is None) — 0·""는 존재하는 id(falsy 함정 회피)
        if strict:
            raise ValueError("flag에 id가 없음 (malformed 골든)")
        fid = fallback_id if fallback_id is not None else ""
    elif not strict:
        fid = str(fid)                                 # 예측 숫자 id는 str로 — 원본 값 보존(추적성) + 타입 통일
    return FlowFlag(
        flag_id=fid,
        type=_coerce_type(d.get("type"), strict=strict),  # 키 누락(None)도 강등 경로로(예측 배치 안 죽게)
        statements=_statements_from_data(d.get("statements"), strict=strict),
        severity=_str_or(d.get("severity"), "medium", "severity", strict=strict),
        title=_str_or(d.get("title"), "", "title", strict=strict),
        topic=_str_or(d.get("topic"), "", "topic", strict=strict),
        explanation=_str_or(d.get("explanation"), "", "explanation", strict=strict),
        resolution=_str_or(d.get("resolution"), "", "resolution", strict=strict),
    )


def _segment_from_data(d: dict) -> TranscriptSegment:
    """골든 전사 세그먼트 파싱 — 항상 엄격(세그먼트는 골든에서만 온다).

    null/비문자열 text가 normalize에서 TypeError 트레이스백을 내는 대신, CLI의 클린 에러
    (rc=2) 경로를 타도록 전부 디스크립티브 ValueError로 거부한다. start_sec 비숫자도 거부 —
    힌트 산술(_pick/_span_grounding)이 무성으로 힌트를 버리는 것을 골든에서는 허용하지 않는다.
    """
    if not isinstance(d, dict):
        raise ValueError(f"segment가 dict가 아님 (malformed 골든): {type(d).__name__}")
    sid = d.get("segment_id")
    if sid is None:
        sid = d.get("id")
    if sid is None:                                    # 존재 검사 — 0·""는 존재하는 id
        raise ValueError("segment에 id가 없음 (malformed 골든)")
    sp = d.get("speaker", "")
    if not isinstance(sp, str):
        raise ValueError(f"segment speaker가 문자열이 아님 (malformed 골든): {type(sp).__name__}")
    text = d.get("text", "")
    if not isinstance(text, str):
        raise ValueError(f"segment text가 문자열이 아님 (malformed 골든): {type(text).__name__}")
    ss = d.get("start_sec", 0.0)
    if not _is_num(ss):
        raise ValueError(f"segment start_sec이 숫자가 아님 (malformed 골든): {ss!r}")
    fl = d.get("flags", ())
    if fl is None:
        fl = ()
    if not isinstance(fl, (list, tuple)):
        raise ValueError(f"segment flags가 리스트가 아님 (malformed 골든): {type(fl).__name__}")
    return TranscriptSegment(
        segment_id=sid,
        speaker=unicodedata.normalize("NFC", sp),      # 화자도 NFC(문서화된 '모두 NFC' 불변식)
        text=unicodedata.normalize("NFC", text),
        start_sec=ss,
        flags=tuple(fl),
    )


def meeting_from_data(data: dict) -> dict:
    """골든 raw dict → {meta, transcript, flags, raw}. 파일 경유 없이 파싱 재사용.

    구조적 오류(비-dict 최상위·transcript/flags null)는 TypeError/AttributeError 트레이스백이
    아니라 디스크립티브 ValueError — CLI가 클린 에러(rc=2)로 거부하게(예측 로더와 대칭).
    """
    if not isinstance(data, dict):
        raise ValueError(f"골든이 dict(JSON 오브젝트)가 아님: {type(data).__name__}")
    meta = data.get("meta", {})
    if not isinstance(meta, dict):                     # 비-dict meta → 소비자(meta.get)가 AttributeError.
        raise ValueError(f"meta가 오브젝트가 아님 (malformed 골든): {type(meta).__name__}")
    transcript = data.get("transcript", [])
    if not isinstance(transcript, list):
        raise ValueError(f"transcript가 리스트가 아님 (malformed 골든): {type(transcript).__name__}")
    flags = data.get("flags", [])
    if not isinstance(flags, list):
        raise ValueError(f"flags가 리스트가 아님 (malformed 골든): {type(flags).__name__}")
    return {
        "meta": meta,
        "transcript": [_segment_from_data(s) for s in transcript],
        "flags": [flag_from_data(f) for f in flags],
        "raw": data,
    }


def load_meeting(path: str | Path) -> dict:
    return meeting_from_data(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def coerce_pred_container(data) -> list:
    """예측 raw(**단일 JSON 값**) → flag dict 리스트. 리스트 또는 {"flags":[...]} 둘 다 허용.

    **파일 로더 전용** 컨테이너 규칙이다 — 어댑터(detect.parse_detection_response)는 자유형식
    텍스트에서 후보를 의미 기반으로 고르므로 자체 규칙을 쓰고, bare 빈 배열 취급이 의도적으로
    다르다(로더=유효한 0건 수용 / 어댑터=모호→클린 에러; 어댑터 독스트링 참조, 3R P13).
    구조적 오류(flags 부재/null/비-list)는 클린 에러."""
    items = data.get("flags") if isinstance(data, dict) else data   # 키 부재도 None→클린 에러(subscript X)
    if not isinstance(items, list):                   # 구조적 오류(flags 부재/null/비-list)는 클린 에러
        raise ValueError("예측이 리스트 또는 {flags:[...]} 형태가 아님")
    return items


def pred_flags_from_items(items: list) -> list:
    """flag dict 리스트 → FlowFlag 리스트(예측 강등 규칙). 파일/어댑터 공용 진입점.

    예측은 변형 라벨/누락 키 per-flag 강등(배치 안 죽게). 비-dict flag는 건너뛰고, id 없으면
    표시용 합성 id. 전량 비-dict(전량 파싱 불가)는 '0건 감지'와 구분해 클린 에러."""
    if not isinstance(items, list):                   # [3R P15] 래핑 dict를 그대로 받으면 키 순회로
        raise ValueError(                             # '전량 파싱 불가' 오진 — 컨테이너 가드를 진입점에
            f"예측 items가 리스트가 아님: {type(items).__name__} "
            "(래핑된 {flags: [...]}는 coerce_pred_container를 먼저 통과시킬 것)")
    out = [flag_from_data(f, strict=False, fallback_id=f"pred{i}")
           for i, f in enumerate(items) if isinstance(f, dict)]
    if items and not out:                             # 전량 비-dict = 구조적 오류(전량 파싱 불가) —
        raise ValueError(                             # '예측 0건'으로 무성 통과하면 벤치 비교가 오염된다
            "예측 flag 원소가 전부 dict가 아님 — 전량 파싱 불가 (0건 감지와 구분)")
    return out


def load_pred_flags(path: str | Path) -> list:
    """예측 flag 로더 — 리스트 또는 {"flags":[...]} 둘 다 허용(Claude 출력 형태 유연)."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return pred_flags_from_items(coerce_pred_container(data))


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
        # 골든은 단일 세그먼트 grounding(span=False) — span 확장은 신뢰 불가 예측 전용 구제책.
        # 경계를 걸치는 골든 인용은 여기서 ungrounded로 시끄럽게 거부되므로 statement를 쪼개 라벨한다.
        segs, ungrounded = resolve_flag_segments(f, transcript, span=False)
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

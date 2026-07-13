"""사전등록(pre-registration) 동결 — 데이터 보기 전에 분석계획을 못박아 사후 선택편향 차단.

freeze_prereg(**fields) → 정렬키 JSON + sha256 콘텐츠 해시. 판정 산출물이 해시를 참조해
'이 사전등록 하 결과'임을 증명한다. 사후에 손잡이(target·estimand 등)를 조용히 바꾸면 해시
불일치로 드러난다(기술적 감지 — 최종 방어는 커밋이력·리뷰). stdlib(json·hashlib)만.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

_SCHEMA_VERSION = "1"
_BENCH_STATS_VERSION = "0.1.0"


@dataclass(frozen=True)
class PreregConfig:
    canonical: str          # 정렬키 compact JSON (바이트 재현)
    content_hash: str
    data: dict


def _canonical(fields: dict) -> str:
    payload = {
        "_schema_version": _SCHEMA_VERSION,
        "_bench_stats_version": _BENCH_STATS_VERSION,
        "fields": fields,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def freeze_prereg(**fields) -> PreregConfig:
    """분석계획 필드를 동결. 버전·스키마를 해시 콘텐츠에 포함(버전 변경도 해시 변경).

    data는 canonical(JSON) 경유로 정규화한다 — freeze의 raw tuple과 load의 JSON list가
    갈라져 라운드트립이 깨지지 않도록 단일 출처(canonical)로 맞춘다.
    """
    canonical = _canonical(fields)
    normalized = json.loads(canonical)["fields"]
    return PreregConfig(canonical=canonical, content_hash=_hash(canonical), data=normalized)


def prereg_hash(cfg: PreregConfig) -> str:
    """저장된 canonical에서 해시를 재계산해 반환(무결성 재검증 겸용)."""
    return _hash(cfg.canonical)


def dump_prereg(cfg: PreregConfig, path) -> None:
    """{content_hash, canonical}을 UTF-8·CRLF 없이 기록(newline='' — T-036 회피)."""
    doc = json.dumps(
        {"content_hash": cfg.content_hash, "canonical": cfg.canonical},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    Path(path).write_text(doc, encoding="utf-8", newline="")


def load_prereg(path) -> PreregConfig:
    """읽어서 canonical에서 해시 재계산 — 저장 해시와 불일치면 tamper로 fail-loud."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    canonical = doc["canonical"]
    stored = doc["content_hash"]
    recomputed = _hash(canonical)
    if recomputed != stored:
        raise ValueError(
            f"사전등록 무결성 실패 — 저장 해시 {stored} ≠ 재계산 {recomputed} (사후 조정 의심)")
    payload = json.loads(canonical)
    return PreregConfig(canonical=canonical, content_hash=stored, data=payload["fields"])

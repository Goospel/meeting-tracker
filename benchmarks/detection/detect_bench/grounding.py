"""Quote grounding — 예측/골든 인용이 전사본에 실재하는지 대조.

**이중 역할**:
  (1) 검증 — Claude가 지어낸 인용(전사에 없는 문장)을 잡아 드롭한다(할루시 방어).
  (2) 해소 — 인용이 어느 전사 세그먼트를 가리키는지 알아내, 감지 채점의 매칭 키로 쓴다.

방법: NFC 정규화 후 (a) 부분일치(인용이 세그먼트 텍스트의 substring) → 강한 grounding,
없으면 (b) 토큰 Jaccard ≥ 임계(경미한 절삭/의역 허용). 둘 다 실패면 ungrounded(None).
순수함수 · 런타임 의존성 0(unicodedata만).
"""

from __future__ import annotations

import unicodedata

_JACCARD_THRESHOLD = 0.6
_PUNCT = " \t\n.,!?…'\"“”‘’()[]{}~-–—:;·"


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _tokens(s: str) -> set:
    return {t.strip(_PUNCT) for t in _nfc(s).split() if t.strip(_PUNCT)}


def ground_quote(quote: str, transcript: list) -> str | None:
    """인용 → 가장 잘 맞는 전사 세그먼트의 id(없으면 None).

    transcript 요소는 .segment_id / .text 속성만 있으면 된다(덕타이핑).
    """
    qn = _nfc(quote).strip()
    if not qn:
        return None
    qtok = _tokens(quote)
    if not qtok:            # 구두점뿐인 절삭 인용('...') — substring으로 아무 세그먼트에나 붙는 것 차단
        return None
    # (a) 완전일치 — 인용이 세그먼트 텍스트 전체와 같으면 가장 강한 신호.
    for seg in transcript:
        if qn == _nfc(seg.text).strip():
            return seg.segment_id
    # (b) 최밀착 부분일치 — 인용을 substring으로 품는 세그먼트 중 인용이 가장 큰 비중을
    #     차지하는(가장 짧은) 세그먼트. '첫 히트' 대신 최적합을 골라 앞선 우연 substring 오귀속 방지.
    best_sub, best_cov = None, 0.0
    for seg in transcript:
        tn = _nfc(seg.text)
        if qn in tn and tn:
            cov = len(qn) / len(tn)
            if cov > best_cov:
                best_sub, best_cov = seg.segment_id, cov
    if best_sub is not None:
        return best_sub
    # (c) 토큰 Jaccard 폴백 — 경미한 절삭/의역.
    best_id, best_j = None, 0.0
    for seg in transcript:
        stok = _tokens(seg.text)
        if not stok:
            continue
        inter = len(qtok & stok)
        if not inter:
            continue
        j = inter / len(qtok | stok)
        if j > best_j:
            best_id, best_j = seg.segment_id, j
    return best_id if best_j >= _JACCARD_THRESHOLD else None


def resolve_flag_segments(flag, transcript: list) -> tuple[frozenset, list]:
    """flag의 각 statement 인용을 전사 세그먼트로 해소.

    반환: (grounding된 segment_id 집합, grounding 실패한 인용 목록).
    빈 집합 = 모든 인용이 할루시(전사에 없음) → 채점에서 즉시 가짜(FP)로.
    """
    segs, ungrounded = set(), []
    for st in flag.statements:
        sid = ground_quote(st.quote, transcript)
        if sid is not None:
            segs.add(sid)
        else:
            ungrounded.append(st.quote)
    return frozenset(segs), ungrounded

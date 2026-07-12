"""Quote grounding — 예측/골든 인용이 전사본에 실재하는지 대조.

**이중 역할**:
  (1) 검증 — Claude가 지어낸 인용(전사에 없는 문장)을 잡아 드롭한다(할루시 방어).
  (2) 해소 — 인용이 어느 전사 세그먼트를 가리키는지 알아내, 감지 채점의 매칭 키로 쓴다.

방법(모두 공백 collapse + NFC 정규화 후 동일 텍스트로 대조):
  단일 세그먼트 — (a) 완전일치 → (b) 최밀착 부분일치 → (c) 토큰 Jaccard ≥ 임계.
  같은 tier에서 후보가 여럿이면(반복 발화) statement의 speaker/time 힌트로 올바른 출현 선택.
  단일이 확실치 않으면(퍼지 Jaccard) 또는 실패하면 — (d) **같은 화자** 인접 세그먼트 경계
  인용을 연속 창(window) substring으로 해소(STT는 한 화자 발화를 쪼개므로 경계 매칭은
  같은 화자에 한정; 교차화자 스티칭은 실재 연속발화가 아니라 거부).
힌트가 비숫자면(예측의 "00:11" 타임스탬프) 무시하고 결정적 첫 출현으로 안전 폴백(크래시 금지).
순수함수 · 런타임 의존성 0(unicodedata만).
"""

from __future__ import annotations

import unicodedata

_JACCARD_THRESHOLD = 0.6
_MAX_SPAN = 3               # 경계 인용이 걸칠 수 있는 최대 연속 세그먼트 수(그 이상은 과매칭 위험)
_PUNCT = " \t\n.,!?…'\"“”‘’()[]{}~-–—:;·"


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _tokens(s: str) -> set:
    return {t.strip(_PUNCT) for t in _nfc(s).split() if t.strip(_PUNCT)}


def _collapse(s: str) -> str:
    return " ".join(_nfc(s).split())


def _num(x):
    """숫자면 그대로, 아니면 None — 신뢰 불가 예측의 문자열 time_sec가 산술을 깨는 것 차단.

    NaN도 강등한다(x == x 비반사성): isinstance 숫자 가드를 통과한 NaN이 최근접 필터의
    == 비교를 전멸시켜 유일 창 grounding까지 죽이는 것 차단(json.loads는 NaN 리터럴 허용).
    """
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x else None


def _single_candidates(qc: str, qtok: set, transcript: list):
    """가장 강한 tier 하나의 후보 세그먼트 인덱스 목록. (tier, [idx]) — 없으면 (None, []).

    tier 0 완전일치 > tier 1 최밀착 부분일치 > tier 2 토큰 Jaccard. 모두 공백 collapse된
    텍스트로 대조(세그먼트 내부 불규칙 공백이 부분일치를 깨는 비대칭 제거). 각 tier 안에서
    동점(반복 발화)이면 후보를 모두 반환하고, 최종 선택은 _pick(힌트)에 맡긴다.
    """
    exact = [i for i, seg in enumerate(transcript) if qc == _collapse(seg.text)]
    if exact:
        return 0, exact

    subs, best_cov = [], 0.0
    for i, seg in enumerate(transcript):
        tc = _collapse(seg.text)
        if tc and qc in tc:
            cov = len(qc) / len(tc)
            subs.append((i, cov))
            if cov > best_cov:
                best_cov = cov
    if subs:
        return 1, [i for i, cov in subs if cov == best_cov]

    js, best_j = [], 0.0
    for i, seg in enumerate(transcript):
        stok = _tokens(seg.text)
        if not stok:
            continue
        inter = len(qtok & stok)
        if not inter:
            continue
        j = inter / len(qtok | stok)
        if j >= _JACCARD_THRESHOLD:
            js.append((i, j))
            if j > best_j:
                best_j = j
    if js:
        return 2, [i for i, j in js if j == best_j]
    return None, []


def _pick(indices: list, transcript: list, speaker, time_sec) -> int:
    """동점 후보 중 하나 선택 — speaker 일치 > time 근접 > 순서(결정성).

    힌트가 없으면(speaker=None/falsy, time_sec None·비숫자) 순서상 첫 후보 = '첫 출현' 보존.
    """
    ts = _num(time_sec)

    def key(i):
        seg = transcript[i]
        spk = 0 if (speaker and getattr(seg, "speaker", "") == speaker) else 1
        sd = _num(getattr(seg, "start_sec", None))
        dt = 0.0
        if ts is not None:                  # 숫자 힌트가 있는데 세그먼트 시각이 불명이면 최하위 —
            dt = abs(sd - ts) if sd is not None else float("inf")   # 0.0(최우선)이면 힌트 역전(span 경로 inf와 정합)
        return (spk, dt, i)

    return min(indices, key=key)


def ground_quote(quote: str, transcript: list, *, speaker=None, time_sec=None) -> str | None:
    """인용 → 가장 잘 맞는 **단일** 전사 세그먼트 id(없으면 None).

    speaker/time_sec은 반복 발화(같은 텍스트가 여러 세그먼트) 동점 해소 힌트.
    경계를 걸친(다중 세그먼트) 인용은 여기서 None — 그건 ground_quote_span이 처리한다.
    transcript 요소는 .segment_id/.text(+선택 .speaker/.start_sec) 속성만 있으면 된다.
    """
    qc = _collapse(quote)
    if not qc:
        return None
    qtok = _tokens(quote)
    if not qtok:            # 구두점뿐인 절삭 인용 — 아무 세그먼트에나 붙는 것 차단
        return None
    _, cands = _single_candidates(qc, qtok, transcript)
    if not cands:
        return None
    return transcript[_pick(cands, transcript, speaker, time_sec)].segment_id


def _same_speaker_windows(qc: str, transcript: list) -> list:
    """qc를 substring으로 품는 '창 내부 화자가 모두 동일한' 최소 연속 창 목록. 각 원소 = (start, size).

    창 화자 동질성은 **전사에서만** 판정한다(seg[i].speaker == seg[i+1].speaker …) — 신뢰 불가한
    예측 화자에 의존하지 않는다. 창 안 한 세그먼트에 통째로 드는 인용은 제외(그건 단일 경로 몫).
    STT는 한 화자 발화를 쪼개므로 경계 인용은 같은 화자 연속 세그먼트 안에서만 참(교차화자
    스티칭은 화자가 갈려 애초에 창이 안 생김). 화자 라벨이 빈/부재면 동질성 판정 자체가 불가 —
    ''=='' 로 교차화자 스티칭이 무성 통과하지 않도록 보수적으로 창을 만들지 않는다.
    """
    norm = [_collapse(s.text) for s in transcript]
    n = len(transcript)
    out = []
    for i in range(n):
        spk = getattr(transcript[i], "speaker", None)
        if not spk:                         # 라벨 없음(빈/부재) = 동질성 판정 불가 → 창 시작 금지
            continue
        for size in range(2, _MAX_SPAN + 1):
            j = i + size - 1
            if j >= n or getattr(transcript[j], "speaker", None) != spk:
                break                       # 창 내부 화자가 갈리면 확장 중단
            concat = " ".join(t for t in norm[i:j + 1] if t)   # 빈 세그먼트(침묵)는 이중 공백 방지 위해 제외
            if qc in concat and not any(qc in norm[k] for k in range(i, j + 1)):
                out.append((i, size))
                break                       # 이 시작점의 최소 창
    return out


def _span_grounding(qc: str, transcript: list, speaker, time_sec) -> frozenset:
    """같은 화자 경계 인용을 창으로 해소 — **모호하지 않을 때만** grounding(벤치 점수 오염 방지).

    예측 speaker/time은 후보를 좁히는 **필터**로만(맞을 때) 쓴다 — 하드 게이트로 쓰면 예측 화자
    라벨이 전사 id와 표기만 달라도 정당한 경계 인용을 통째로 놓친다. 필터 후에도 서로 다른 창이
    여럿 남으면(위치 힌트 부재로 애매) 추측으로 틀린 창에 귀속시키느니 grounding하지 않는다.
    """
    cands = _same_speaker_windows(qc, transcript)
    if not cands:
        return frozenset()

    segset = {c: frozenset(transcript[k].segment_id for k in range(c[0], c[0] + c[1]))
              for c in cands}
    # 중첩 창 축약 — 다른 후보의 상위집합(더 큰 창)은 최소 커버 창에 흡수. STT가 한 화자를 3+
    # 세그먼트로 쪼갠 런에서 같은 위치의 min/superset 창을 '서로 다른 후보'로 세어 모호로
    # 오판하는 것을 막는다(같은 위치 = 한 결과).
    allsets = list(segset.values())
    cands = [c for c in cands if not any(o < segset[c] for o in allsets)]

    if speaker:                             # 예측 화자와 맞는 창이 있으면 그걸로 좁힘(맞을 때만)
        matched = [c for c in cands if getattr(transcript[c[0]], "speaker", None) == speaker]
        if matched:
            cands = matched

    ts = _num(time_sec)
    if ts is not None:                      # 숫자 time 힌트가 있으면 최근접 창만
        def dist(c):
            # 경계 인용은 정의상 여러 세그먼트에 걸침 — 힌트가 창의 뒤쪽 세그먼트 시각을
            # 가리켜도 정당하므로, 첫 세그먼트가 아니라 창 내 최근접 세그먼트 거리로 잰다.
            best_d = float("inf")
            for k in range(c[0], c[0] + c[1]):
                sd = _num(getattr(transcript[k], "start_sec", None))
                if sd is not None:
                    best_d = min(best_d, abs(sd - ts))
            return best_d
        best = min(dist(c) for c in cands)
        cands = [c for c in cands if dist(c) == best]

    distinct = {segset[c] for c in cands}
    if len(distinct) == 1:                  # 유일(또는 동일 결과) → 확정
        return next(iter(distinct))
    return frozenset()                      # 서로 다른 위치로 모호 → grounding 안 함


def ground_quote_span(quote: str, transcript: list, *, speaker=None, time_sec=None) -> frozenset:
    """인용 → grounding되는 전사 세그먼트 id 집합(단일이면 1개, 경계 인용이면 여럿, 없으면 빈 집합).

    확실한 단일 매칭(완전일치/부분일치)이면 그대로 반환. 퍼지(Jaccard) 단일이면, 그 세그먼트를
    **포함하는** 경계 창이 있을 때만 span으로 확장(토큰이 한 세그먼트에 몰려도 파트너 세그먼트를
    회수; 무관한 창이 정답 단일을 가로채는 것은 차단). 단일 후보가 아예 없으면 순수 경계 span.
    resolve_flag_segments가 flag statement별로 이걸 부른다.
    """
    qc = _collapse(quote)
    if not qc:
        return frozenset()
    qtok = _tokens(quote)
    if not qtok:                            # 빈/구두점뿐 인용은 span으로도 붙지 않음
        return frozenset()
    tier, cands = _single_candidates(qc, qtok, transcript)
    if cands:
        single = transcript[_pick(cands, transcript, speaker, time_sec)].segment_id
        if tier in (0, 1):                  # 확실한 단일 — 그대로
            return frozenset({single})
        span = _span_grounding(qc, transcript, speaker, time_sec)   # tier 2 퍼지
        # 동점 후보 '어느 하나라도' 창에 포함되면 확장 — _pick이 고른 하나만 보면 동점(반복
        # 발화)에서 창 밖 첫 출현이 verbatim 창을 가로챈다. 창 밖 단독 최고 후보는 여전히 유지.
        if span and any(transcript[i].segment_id in span for i in cands):
            return span
        return frozenset({single})          # 무관한 창이면 퍼지 단일 유지(하이재킹 방지)
    return _span_grounding(qc, transcript, speaker, time_sec)       # 순수 경계 인용


def resolve_flag_segments(flag, transcript: list, *, span: bool = True) -> tuple[frozenset, list]:
    """flag의 각 statement 인용을 전사 세그먼트로 해소.

    반환: (grounding된 segment_id 집합, grounding 실패한 인용 목록).
    빈 집합 = 모든 인용이 할루시(전사에 없음) → 채점에서 즉시 가짜(FP)로.
    statement의 speaker/time_sec을 grounding 힌트로 전달(반복 발화 오귀속 방지, 경계 span 화자 제약).

    span=False = 골든 경로(단일 세그먼트 grounding만, main 의미론 + 힌트) — 골든의 퍼지 인용이
    이웃 세그먼트로 부풀어 validate 게이트·채점 segset을 오염시키지 않게. 경계를 걸치는 골든
    인용은 statement를 세그먼트별로 쪼개 라벨한다(span 확장은 신뢰 불가 예측 전용 구제책).
    빈 인용(예측 quote:null 강등)은 '전사에 없는 인용(할루시)'이 아니라 '인용 자체가 없음' —
    ungrounded 목록에 넣지 않는다(채점기가 no_evidence로 분리).
    """
    segs, ungrounded = set(), []
    for st in flag.statements:
        quote = st.quote
        if not (isinstance(quote, str) and quote.strip()):
            continue                        # 인용 자체가 없음 — 할루시(ungrounded)와 구분
        speaker = getattr(st, "speaker", None)
        time_sec = getattr(st, "time_sec", None)
        if span:
            got = ground_quote_span(quote, transcript, speaker=speaker, time_sec=time_sec)
        else:
            sid = ground_quote(quote, transcript, speaker=speaker, time_sec=time_sec)
            got = frozenset({sid}) if sid is not None else frozenset()
        if got:
            segs |= got
        else:
            ungrounded.append(quote)
    return frozenset(segs), ungrounded

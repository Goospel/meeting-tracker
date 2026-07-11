"""결정적 Levenshtein 기반 CER(문자오류율).

전체 CER은 '새너티/맥락용 보조지표'다 — 필러 오인식과 '3천만원→2천만원'을 같은
무게로 세므로 제품의 1순위 KPI가 될 수 없다(그건 score.py의 CTER). 여기서는
정규화 전(N0)/후(N2)/자모 CER과 결정적 정렬을 제공한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from .normalize import jamo_tokens, normalize_text, syllable_tokens, to_nfc

# 정렬 연산: (op, ref_idx|None, hyp_idx|None)
Op = tuple


def align_ops(ref: list[str], hyp: list[str]) -> list[Op]:
    """Levenshtein 편집 경로를 결정적으로 복원.

    타이브레이크(비용 동률 시): 대각선(match/sub) > 상(del) > 좌(ins) 고정 순서로
    비결정성을 제거한다. 반환은 (op, i, j) 튜플 리스트.
    """
    n, m = len(ref), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i
    for j in range(1, m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        ri = ref[i - 1]
        row, prev = dp[i], dp[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ri == hyp[j - 1] else 1
            row[j] = min(prev[j - 1] + cost, prev[j] + 1, row[j - 1] + 1)

    ops: list[Op] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            if dp[i][j] == dp[i - 1][j - 1] + cost:
                ops.append(("match" if cost == 0 else "sub", i - 1, j - 1))
                i -= 1
                j -= 1
                continue
        if i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            ops.append(("del", i - 1, None))
            i -= 1
            continue
        ops.append(("ins", None, j - 1))
        j -= 1
    ops.reverse()
    return ops


def _counts(ops: list[Op]) -> tuple[int, int, int]:
    s = sum(1 for op, _, _ in ops if op == "sub")
    d = sum(1 for op, _, _ in ops if op == "del")
    i = sum(1 for op, _, _ in ops if op == "ins")
    return s, d, i


def _rate(ref: list[str], hyp: list[str]) -> tuple[float, int, int, int]:
    s, d, i = _counts(align_ops(ref, hyp))
    n = len(ref)
    if n == 0:
        # 레퍼런스 없음: rate 미정의 → hyp 비면 0.0, 아니면 무한대(원시 삽입수는 별도 보고).
        return (0.0 if len(hyp) == 0 else float("inf")), s, d, i
    return (s + d + i) / n, s, d, i


@dataclass
class CerResult:
    n_ref: int
    substitutions: int
    deletions: int
    insertions: int
    raw: float      # N0: 정규화 전 음절 CER
    norm: float     # N2: 정규화 후 음절 CER
    jamo: float     # 자모 진단 CER
    outlier: bool   # raw > 1.0 (환각형 삽입 등) → 평균 오염 경고


def cer(ref: str, hyp: str) -> CerResult:
    """(ref, hyp) 문자열의 CER 결과를 계산."""
    raw_ref, raw_hyp = syllable_tokens(ref), syllable_tokens(hyp)
    raw, s, d, i = _rate(raw_ref, raw_hyp)

    norm, *_ = _rate(syllable_tokens(normalize_text(ref)), syllable_tokens(normalize_text(hyp)))
    jamo, *_ = _rate(jamo_tokens(ref), jamo_tokens(hyp))

    return CerResult(
        n_ref=len(raw_ref),
        substitutions=s,
        deletions=d,
        insertions=i,
        raw=raw,
        norm=norm,
        jamo=jamo,
        outlier=raw > 1.0,
    )

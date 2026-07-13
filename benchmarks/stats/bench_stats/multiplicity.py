"""다중비교 FWER 통제 — Holm-Bonferroni step-down.

per-type(모순/번복/미해결/재논의) 등 여러 검정을 한 가족으로 볼 때 family-wise 오류율을
alpha로 통제한다. 분포무관·임의종속에서 유효·결정적·보수적. stdlib만.
"""

from __future__ import annotations

from typing import Sequence


def holm_adjust(pvalues: Sequence[float], alpha: float = 0.05):
    """(adjusted_p, reject) 튜플. 입력 순서 그대로 반환.

    오름차순 정렬 후 i번째(0-index) 조정 = (m-i)·p_(i) 를 누적최대(cummax)해 단조화, 1로 캡.
    reject = adjusted ≤ alpha. known: [0.01,0.02,0.03,0.04]→adj (0.04,0.06,0.06,0.06), rej (T,F,F,F).
    """
    m = len(pvalues)
    if m == 0:
        return (), ()
    if not all(0.0 <= p <= 1.0 for p in pvalues):
        raise ValueError("p값은 [0,1]")
    order = sorted(range(m), key=lambda i: pvalues[i])
    adj_sorted = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        val = min(1.0, (m - rank) * pvalues[idx])
        running = max(running, val)          # step-down 단조화
        adj_sorted[rank] = running
    adjusted = [0.0] * m
    for rank, idx in enumerate(order):
        adjusted[idx] = adj_sorted[rank]
    reject = tuple(a <= alpha for a in adjusted)
    return tuple(adjusted), reject

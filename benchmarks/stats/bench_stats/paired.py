"""쌍체(두 감지기/두 config) 비교 — 정확 McNemar와 clustering-정직 부호치환.

두 층위:
  (1) flag 수준 mcnemar_exact(b,c): 불일치쌍에 정확이항 부호검정. 정확하지만 flag가 회의에
      군집됨을 무시(양의 회의내 상관에서 anticonservative) → 서술·known-answer 앵커 전용.
  (2) 회의(교환단위) 수준 paired_cluster_permutation: 회의별 부호를 뒤집는 2^n 배치를 전열거해
      clustering을 존중 → **판정용 1차 검정**.

킬러 정직성: min_attainable_two_sided_p(n)=2/2ⁿ 와 comparison_floor_n(α)=⌈log₂(2/α)⌉.
n=3이면 관측 무관 최소 p=0.25>0.05라 항상 UNDERPOWERED.

stdlib(fractions·itertools·math·random)만. n≤max_exact면 전열거로 결정적(seed 무관).
"""

from __future__ import annotations

import itertools
import random
from fractions import Fraction
from typing import Callable, Sequence

from .types import McNemarResult, PairedClusterBinary, SignFloor


def min_attainable_two_sided_p(n_units: int) -> float:
    """부호검정 계열이 n_units 교환단위로 낼 수 있는 **최소** 양측 p = min(1, 2·2⁻ⁿ).

    정확값(Fraction)으로 계산 후 float 반환. n=3→0.25, n=6→2/64, n≤1→1.0.
    """
    if n_units < 0:
        raise ValueError(f"n_units≥0 — got {n_units}")
    return float(min(Fraction(1), 2 * Fraction(1, 2) ** n_units))


def comparison_floor_n(alpha: float = 0.05) -> int:
    """양측 부호치환에서 유의가 원리적으로 가능해지는 최소 교환단위(회의) 수.

    = 가장 작은 n such that min_attainable_two_sided_p(n) ≤ alpha (= ⌈log₂(2/α)⌉).
    α=0.05→6, 0.01→8, 0.10→5. min_attainable와 정의상 일관.
    """
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha∈(0,1) — got {alpha}")
    n = 1
    while min_attainable_two_sided_p(n) > alpha:
        n += 1
    return n


def sign_floor(n_clusters: int, alpha: float = 0.05) -> SignFloor:
    """이 층의 킬러 정직성 산출 — n_clusters에서 부호치환 최소 양측 p와 α 도달 가능성."""
    mp = min_attainable_two_sided_p(n_clusters)
    return SignFloor(
        n_clusters=n_clusters,
        min_two_sided_p=mp,
        alpha=alpha,
        alpha_reachable=(mp <= alpha),
        min_clusters_to_reach_alpha=comparison_floor_n(alpha),
    )


def mcnemar_exact(b: int, c: int) -> McNemarResult:
    """정확 McNemar(부호검정). n=b+c 불일치쌍에 조건화한 정확이항 양측 p.

    p = min(1, 2·Σ_{k=0..min(b,c)} C(n,k)·2⁻ⁿ). 연속성보정 없음(χ² 근사용이라 정확이항엔 불요).
    대칭 mcnemar_exact(b,c)==(c,b). b+c=0 → p=1.0.
    """
    if b < 0 or c < 0:
        raise ValueError(f"b,c≥0 — got b={b}, c={c}")
    n = b + c
    if n == 0:
        p = 1.0
    else:
        m = min(b, c)
        from math import comb

        tail = sum(comb(n, k) for k in range(m + 1))
        p = float(min(Fraction(1), Fraction(2 * tail, 2 ** n)))
    return McNemarResult(
        b=b, c=c, n_discordant=n, p_two_sided=p,
        method="exact-binomial-sign",
        min_attainable_p=min_attainable_two_sided_p(n),
        warnings=("clustering 무시 — anticonservative; 판정은 paired_cluster_permutation 사용",),
    )


def default_net_discordance(signs: Sequence[int], d: Sequence[int]) -> int:
    """검정통계량 = |Σ s_c·d_c| — 회의별 순 불일치(A맞힘-B맞힘)에 부호를 씌운 합의 절댓값."""
    return abs(sum(s * dc for s, dc in zip(signs, d)))


def cluster_sign_test(
    diffs: Sequence[int],
    statistic: Callable[[Sequence[int], Sequence[int]], float] = default_net_discordance,
    *,
    max_exact_clusters: int = 20,
    n_mc: int = 20000,
    seed: int = 0,
) -> McNemarResult:
    """회의별 순 차이(A맞힘-B맞힘) 벡터에 대한 부호치환검정.

    교환단위=회의; 회의별 부호뒤집기 2ⁿ 배치를 n≤max_exact면 전열거(결정적·seed 무관), 아니면
    seed 고정 MC. min_attainable_p로 소 n 자동 UNDERPOWERED. 관측 통계 이상(≥) 비율 = 양측 p.
    """
    d = list(diffs)
    n = len(d)
    if n == 0:
        return McNemarResult(0, 0, 0, 1.0, "cluster-sign-permutation-empty",
                             min_attainable_two_sided_p(0), warnings=("빈 코퍼스",))
    b = sum(1 for x in d if x > 0)   # A 우세 회의 수
    c = sum(1 for x in d if x < 0)   # B 우세 회의 수
    t_obs = statistic([1] * n, d)

    if n <= max_exact_clusters:
        total = 2 ** n
        ge = sum(1 for signs in itertools.product((1, -1), repeat=n)
                 if statistic(signs, d) >= t_obs)
        p = ge / total
        method = "cluster-sign-permutation-exact"
    else:
        rng = random.Random(seed)
        ge = 0
        for _ in range(n_mc):
            signs = [rng.choice((1, -1)) for _ in range(n)]
            if statistic(signs, d) >= t_obs:
                ge += 1
        # +1 보정: 관측(항등) 배치는 항상 ≥t_obs이므로 p≥1/(n_mc+1). 무효한 p=0 방지.
        p = (ge + 1) / (n_mc + 1)
        method = "cluster-sign-permutation-mc"

    return McNemarResult(
        b=b, c=c, n_discordant=b + c, p_two_sided=min(1.0, p), method=method,
        min_attainable_p=min_attainable_two_sided_p(n),
    )


def paired_cluster_permutation(
    clusters: Sequence[PairedClusterBinary],
    statistic: Callable[[Sequence[int], Sequence[int]], float] = default_net_discordance,
    *,
    two_sided: bool = True,
    max_exact_clusters: int = 20,
    n_mc: int = 20000,
    seed: int = 0,
) -> McNemarResult:
    """clustering-정직 1차 검정 — PairedClusterBinary의 회의별 net_discordance에 부호치환.

    cluster_sign_test로 위임(카운트 차이 경로와 단일 구현).
    """
    return cluster_sign_test(
        [cl.net_discordance for cl in clusters], statistic,
        max_exact_clusters=max_exact_clusters, n_mc=n_mc, seed=seed)

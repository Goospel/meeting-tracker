"""회의(cluster) 수준 재샘플 CI + pooled Clopper-Pearson.

- cluster_bootstrap_ci: 회의를 통째 복원추출. n≤max_exact면 nⁿ 전열거로 정확 percentile(RNG 없음,
  결정적); 초과면 seed 고정 MC. BCa는 n≥bca_min에서만·가드 통과 시, 아니면 강등/퇴화.
- pooled_proportion_ci: Σ카운트에 Clopper-Pearson — clustering을 무시하므로 '정직한 CI'가 아니라
  '불확실성 폭의 낙관적 하한'으로만 라벨한다.

**결정성(seed) ≠ 통계적 정밀**: n_distinct_resamples·degenerate·granular를 강제 노출해,
매끄러운 숫자가 정밀로 오인되지 않게 한다. stdlib(math·random·statistics·itertools)만.
"""

from __future__ import annotations

import itertools
import math
import random
from statistics import fmean
from typing import Sequence

from .exact import clopper_pearson, phi_inv
from .types import BootstrapCI, ClusterBinary, ExactCI

_EPS = 1e-12
_GRANULAR_MAX = 10   # n_distinct_resamples ≤ 이면 격자가 조악 → granular 경고


def _stat(estimand: str):
    """estimand → (list[ClusterBinary] → float) 통계량."""
    if estimand == "meeting_weighted":
        def f(cs):
            rs = [c.successes / c.n for c in cs if c.n > 0]
            return fmean(rs) if rs else math.nan
        return f
    if estimand == "flag_weighted":
        def f(cs):
            sn = sum(c.n for c in cs)
            return sum(c.successes for c in cs) / sn if sn > 0 else math.nan
        return f
    raise ValueError(f"estimand∈{{meeting_weighted,flag_weighted}} — got {estimand!r}")


def _percentile(sorted_vals: list[float], q: float) -> float:
    """nearest-rank 분위 — 재현 가능한 고정 규약(보간 없음)."""
    n = len(sorted_vals)
    rank = min(max(math.ceil(q * n), 1), n)
    return sorted_vals[rank - 1]


def _resample_distribution(clusters, stat, *, max_exact, n_boot, seed):
    """(정렬된 통계량 리스트, 구분 재샘플 수, RNG사용여부)."""
    n = len(clusters)
    distinct = set()
    vals = []
    if n <= max_exact:
        for combo in itertools.product(range(n), repeat=n):
            vals.append(stat([clusters[i] for i in combo]))
            distinct.add(tuple(sorted(combo)))
        used_rng = False
    else:
        rng = random.Random(seed)
        for _ in range(n_boot):
            combo = [rng.randrange(n) for _ in range(n)]
            vals.append(stat([clusters[i] for i in combo]))
            distinct.add(tuple(sorted(combo)))
        used_rng = True
    vals.sort()
    return vals, len(distinct), used_rng


def _bca_bounds(clusters, stat, point, vals, level):
    """BCa 하/상한 분위 α₁, α₂ 계산. 붕괴(z0 비유한·가속 분모≈0) 시 None."""
    b = len(vals)
    n_less = sum(1 for v in vals if v < point)
    prop = n_less / b
    if prop <= 0.0 or prop >= 1.0:
        return None                       # point-mass/극단 → z0=±inf
    z0 = phi_inv(prop)
    # 가속 a = leave-one-cluster-out jackknife 왜도.
    jack = []
    for i in range(len(clusters)):
        sub = clusters[:i] + clusters[i + 1:]
        jack.append(stat(sub))
    jbar = fmean(jack)
    num = sum((jbar - j) ** 3 for j in jack)
    den = 6.0 * (sum((jbar - j) ** 2 for j in jack)) ** 1.5
    if abs(den) < _EPS:
        return None                       # 3점 jackknife 공선 → 분모≈0
    a = num / den
    za2 = phi_inv(1.0 - (1.0 - level) / 2.0)

    def adj(z):
        return z0 + (z0 + z) / (1.0 - a * (z0 + z))

    from math import erf, sqrt
    def Phi(x):
        return 0.5 * (1.0 + erf(x / sqrt(2.0)))
    a1 = Phi(adj(-za2))
    a2 = Phi(adj(za2))
    if not (0.0 < a1 < a2 < 1.0):
        return None
    return _percentile(vals, a1), _percentile(vals, a2)


def cluster_bootstrap_ci(
    clusters: Sequence[ClusterBinary],
    estimand: str = "meeting_weighted",
    *,
    level: float = 0.95,
    method: str = "auto",
    n_boot: int = 10000,
    seed: int = 0,
    max_exact_clusters: int = 6,
    bca_min_clusters: int = 10,
) -> BootstrapCI:
    """회의 복원추출 진단 구간. n≤max_exact면 전열거(결정적), 초과면 MC.

    method='auto': 전열거/MC percentile(BCa 자동 안 탐). method='bca': n≥bca_min·비퇴화에서만
    실제 BCa, 아니면 'degraded_percentile' 또는 point-mass 퇴화(nan). 소 n엔 granular 경고 강제.
    """
    if not (0.0 < level < 1.0):
        raise ValueError(f"level∈(0,1) 필요 — got {level} (조용한 퇴화 구간 금지)")
    all_clusters = list(clusters)
    if not all_clusters:
        raise ValueError("빈 코퍼스 — 부트스트랩 불가")
    # n=0 회의는 지표에 기여하지 않아 리샘플 통계량을 NaN으로 오염시킨다(pooled_proportion_ci와
    # 동일하게) 재샘플 단위에서 제외 — 조용한 NaN 금지.
    clusters = [c for c in all_clusters if c.n > 0]
    if not clusters:
        raise ValueError("전 클러스터 n=0 — 분모 0, 부트스트랩 불가(조용한 NaN 금지)")
    n = len(clusters)
    stat = _stat(estimand)
    point = stat(list(clusters))
    vals, n_distinct, _ = _resample_distribution(
        clusters, stat, max_exact=max_exact_clusters, n_boot=n_boot, seed=seed)

    warnings: list[str] = []
    if len(all_clusters) > n:
        warnings.append(f"빈 회의(n=0) {len(all_clusters) - n}개 재샘플에서 제외(빈 회의)")
    if n_distinct <= _GRANULAR_MAX:
        warnings.append(
            f"granular: n_distinct_resamples={n_distinct} — 결정성(seed)≠통계적 정밀; "
            f"판정용 구간 아님(진단만)")
    spread = (max(vals) - min(vals)) if vals else 0.0
    point_mass = spread < _EPS

    a2 = (1.0 - level) / 2.0
    base_method = "exact_enum_percentile" if n <= max_exact_clusters else "percentile"

    if method == "bca":
        if point_mass:
            return BootstrapCI(
                point=point, lower=math.nan, upper=math.nan, level=level, method="bca",
                n_boot=len(vals), n_distinct_resamples=n_distinct, degenerate=True,
                seed=seed,
                warnings=tuple(warnings) + (
                    "BCa undefined: point-mass (예 FP=0 precision) — 유한 구간 미반환; "
                    "zero_event_upper_bound 사용",))
        if n < bca_min_clusters:
            lower, upper = _percentile(vals, a2), _percentile(vals, 1.0 - a2)
            return BootstrapCI(
                point=point, lower=lower, upper=upper, level=level,
                method="degraded_percentile", n_boot=len(vals),
                n_distinct_resamples=n_distinct, degenerate=False, seed=seed,
                warnings=tuple(warnings) + (
                    f"bca_unstable: <{bca_min_clusters} clusters — percentile로 강등"
                    "(BCa z0/가속이 소 cluster에서 붕괴)",))
        bounds = _bca_bounds(list(clusters), stat, point, vals, level)
        if bounds is None:
            lower, upper = _percentile(vals, a2), _percentile(vals, 1.0 - a2)
            return BootstrapCI(
                point=point, lower=lower, upper=upper, level=level,
                method="degraded_percentile", n_boot=len(vals),
                n_distinct_resamples=n_distinct, degenerate=False, seed=seed,
                warnings=tuple(warnings) + ("bca_unstable: z0/가속 붕괴 — percentile 강등",))
        lower, upper = bounds
        return BootstrapCI(
            point=point, lower=lower, upper=upper, level=level, method="bca",
            n_boot=len(vals), n_distinct_resamples=n_distinct, degenerate=False,
            seed=seed, warnings=tuple(warnings))

    # method='auto'/'percentile'
    lower, upper = _percentile(vals, a2), _percentile(vals, 1.0 - a2)
    if point_mass:
        warnings.append(
            "point-mass — bootstrap 구간은 인공물(예 FP=0 → [1,1]); "
            "zero_event_upper_bound로 보고")
    return BootstrapCI(
        point=point, lower=lower, upper=upper, level=level, method=base_method,
        n_boot=len(vals), n_distinct_resamples=n_distinct, degenerate=point_mass,
        seed=seed, warnings=tuple(warnings))


def pooled_proportion_ci(
    clusters: Sequence[ClusterBinary],
    *,
    alpha: float = 0.05,
    estimand: str = "meeting_weighted",
) -> ExactCI:
    """Σ카운트에 Clopper-Pearson. clustering 무시 → '폭의 낙관적 하한'으로만 라벨.

    point는 estimand대로(flag_weighted=Σk/Σn, meeting_weighted=회의별 비율 비가중 평균);
    구간은 두 경우 모두 pooled 카운트 CP(회의간 분산 미반영). verdict 게이트 없이 단독 인용 금지.
    """
    if not clusters:
        raise ValueError("빈 코퍼스 — pooled CI 불가")
    nonempty = [c for c in clusters if c.n > 0]
    sk = sum(c.successes for c in nonempty)
    sn = sum(c.n for c in nonempty)
    if sn == 0:
        raise ValueError("전 클러스터 n=0 — 분모 0, 조용한 0.0 금지")

    warnings = [
        "clopper-pearson-pooled: clustering 무시 — 이 구간은 정직한 CI가 아니라 "
        "'불확실성 폭의 낙관적(좁은) 하한'; 참 구간은 더 넓다"]
    if len(nonempty) < len(clusters):
        warnings.append(f"n=0 클러스터 {len(clusters) - len(nonempty)}개 분모에서 스킵(빈 회의)")

    if estimand == "flag_weighted":
        point = sk / sn
    elif estimand == "meeting_weighted":
        point = fmean([c.successes / c.n for c in nonempty])
        warnings.append(
            "point는 meeting_weighted이나 구간은 pooled 카운트 CP — 가중 불일치(폭 진단용)")
    else:
        raise ValueError(f"estimand∈{{flag_weighted,meeting_weighted}} — got {estimand!r}")

    lower, upper = clopper_pearson(sk, sn, alpha=alpha)
    return ExactCI(
        point=point, lower=lower, upper=upper, level=1.0 - alpha,
        method="clopper-pearson-pooled", estimand=estimand,
        n_clusters=len(clusters), n_items=sn, warnings=tuple(warnings))

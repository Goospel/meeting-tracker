"""검정력·MDE·수집목표(중단규칙).

두 경로:
  (A) 쌍체 비교 검정력 — simulate_paired_power/mde_paired: cluster 치환엔 폐형식 검정력이 없어
      사전등록 생성모형(baseline·effect·회의당 flag수·icc·seed)에서 seed 고정 몬테카를로. 구조적
      floor(2/2ⁿ>alpha)면 모든 effect에서 power=0 → MDE 부존재.
  (B) 단일 감지기/CI 정밀 MDE — mde_exact_binomial: 데이터 독립. 정확이항 단측 임계 k_crit 후
      목표 검정력 달성 최소 p1. clustering은 n_eff=floor(n_items/deff)로 정직 반영(deff는 가정).

데이터 독립인 (B)와 assumption-free한 comparison_floor만이 n=3에서 통계적으로 타당한 전향 산출.
stdlib(math·random)만. icc는 사전등록 가정 — 추정 아님(반환에 명시).
"""

from __future__ import annotations

import math
import random
from typing import Sequence

from .paired import (
    comparison_floor_n,
    min_attainable_two_sided_p,
    paired_cluster_permutation,
)
from .types import CollectionTarget, MDE, PowerResult, PairedClusterBinary


def _binom_sf(k: int, n: int, p: float) -> float:
    """P(X≥k | X~Bin(n,p))."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))


# ── (B) 데이터 독립 정확이항 MDE ─────────────────────────────────────────────
def mde_exact_binomial(
    n_items: int, p0: float, *, alpha: float = 0.05, power: float = 0.8,
    direction: str = "greater", deff: float = 1.0,
) -> MDE:
    """H0:p=p0 정확이항 단측 검정에서 목표 검정력을 주는 최소 |p1-p0|.

    n_eff=floor(n_items/deff). k_crit=min{k: P(X≥k|p0)≤alpha}. 이후 P(X≥k_crit|p1)≥power 최소 p1.
    단조: n_items↑→mde↓, deff↑→mde↑. deff는 사전등록 상수(가정)임을 detail에 명시.
    """
    if direction != "greater":
        raise ValueError("v1은 direction='greater'만 지원")
    if not (0.0 < p0 < 1.0):
        raise ValueError(f"p0∈(0,1) — got {p0}")
    if deff < 1.0:
        raise ValueError(f"deff≥1 — got {deff}")
    # float 바닥나눗셈은 표현오차로 off-by-one(예: int(11//1.1)=9, 참 floor=10) → eps 스냅.
    n_eff = math.floor(n_items / deff + 1e-9)
    detail = {"deff": deff, "deff_note": "사전등록 가정(추정 아님)", "n_items": n_items}
    if n_eff < 1:
        return MDE(None, "n_eff<1", seed=0, n_eff=n_eff, detail=detail)
    # 기각역: X≥k_crit 이 H0에서 alpha 이하.
    k_crit = None
    for k in range(1, n_eff + 1):
        if _binom_sf(k, n_eff, p0) <= alpha:
            k_crit = k
            break
    if k_crit is None:
        return MDE(None, "no_rejection_region", seed=0, n_eff=n_eff, detail=detail)
    # 목표 검정력 최소 p1 (단조 → 이분법).
    lo, hi = p0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _binom_sf(k_crit, n_eff, mid) >= power:
            hi = mid
        else:
            lo = mid
    p1 = (lo + hi) / 2.0
    return MDE(p1 - p0, "exact_binomial", seed=0, n_eff=n_eff, k_crit=k_crit, detail=detail)


# ── (A) 쌍체 검정력 몬테카를로 ───────────────────────────────────────────────
def _simulate_paired_dataset(rng, n_clusters, p_a, p_b, m, icc):
    """icc(회의내 상관) 근사: 회의별 flag가 확률 λ로 회의공용 동전을 공유.

    두 flag가 공용 동전을 쓰는 건 둘 다 독립적으로 공유를 택할 때(확률 λ²)뿐이라 실현 상관은
    λ²다. 목표 상관 icc를 얻으려면 λ=√icc를 쓴다(λ=icc면 상관이 icc²로 한 자릿수 붕괴).
    """
    lam = math.sqrt(icc)
    clusters = []
    for ci in range(n_clusters):
        shared_a = rng.random() < p_a
        shared_b = rng.random() < p_b
        a, b = [], []
        for _ in range(m):
            a.append(shared_a if rng.random() < lam else (rng.random() < p_a))
            b.append(shared_b if rng.random() < lam else (rng.random() < p_b))
        clusters.append(PairedClusterBinary(f"m{ci}", tuple(a), tuple(b)))
    return clusters


def simulate_paired_power(
    *, n_clusters: int, baseline: float, effect: float, flags_per_cluster: int,
    icc: float, alpha: float = 0.05, n_sim: int = 2000, seed: int = 0,
) -> PowerResult:
    """H1(B=baseline+effect, 회의내 상관 icc)에서 사전등록 쌍체치환검정의 검정력.

    floor_p=2/2ⁿ>alpha면 effect 무관 power=0.0 강제(구조적 유의 불가).
    baseline+effect>1이면 조용한 클리핑(명목 effect≠실제) 대신 fail-loud.
    """
    if not (0.0 <= baseline <= 1.0):
        raise ValueError(f"baseline∈[0,1] — got {baseline}")
    if not (0.0 <= icc <= 1.0):
        raise ValueError(f"icc∈[0,1] — got {icc}")
    if not (0.0 <= baseline + effect <= 1.0 + 1e-12):
        raise ValueError(
            f"baseline+effect가 확률 범위[0,1] 이탈 — p_b={baseline + effect} "
            "(효과를 조용히 클리핑하면 보고 effect가 실제와 어긋남)")
    floor_p = min_attainable_two_sided_p(n_clusters)
    if floor_p > alpha:
        return PowerResult(effect, 0.0, floor_p, False, seed, n_clusters, alpha)
    p_a = baseline
    p_b = min(1.0, baseline + effect)
    rng = random.Random(seed)
    rej = 0
    for _ in range(n_sim):
        ds = _simulate_paired_dataset(rng, n_clusters, p_a, p_b, flags_per_cluster, icc)
        r = paired_cluster_permutation(ds, seed=rng.randint(0, 2**31 - 1))
        if r.p_two_sided <= alpha:
            rej += 1
    return PowerResult(effect, rej / n_sim, floor_p, True, seed, n_clusters, alpha)


def mde_paired(
    *, n_clusters: int, baseline: float, target_power: float = 0.8,
    flags_per_cluster: int, icc: float, alpha: float = 0.05,
    effect_grid: Sequence[float], n_sim: int = 2000, seed: int = 0,
) -> MDE:
    """effect_grid 오름차순 스캔 → power≥target 최소 effect. floor 미달이면 mde=None."""
    floor_p = min_attainable_two_sided_p(n_clusters)
    grid = tuple(sorted(effect_grid))
    if grid and baseline + grid[-1] > 1.0 + 1e-9:
        raise ValueError(
            f"effect_grid가 p_b>1 유발 — baseline={baseline}, max effect={grid[-1]} (확률 범위 초과)")
    if floor_p > alpha:
        return MDE(None, "alpha_unreachable_at_n", seed=seed, grid=grid,
                   detail={"floor_p": floor_p, "grid_power": {e: 0.0 for e in grid}})
    powers = {}
    for e in grid:
        pr = simulate_paired_power(
            n_clusters=n_clusters, baseline=baseline, effect=e,
            flags_per_cluster=flags_per_cluster, icc=icc, alpha=alpha,
            n_sim=n_sim, seed=seed)
        powers[e] = pr.power
        if pr.power >= target_power:
            return MDE(e, "simulated", seed=seed, grid=grid,
                       detail={"grid_power": powers, "icc_assumed": icc})
    return MDE(None, "insufficient_power_over_grid", seed=seed, grid=grid,
               detail={"grid_power": powers, "icc_assumed": icc})


# ── 수집목표(중단규칙) ───────────────────────────────────────────────────────
def _cp_half_width(k: int, n: int, alpha: float) -> float:
    from .exact import clopper_pearson
    lo, hi = clopper_pearson(k, n, alpha=alpha)
    return (hi - lo) / 2.0


def _n_iid_for_precision(half_width_target: float, p: float, alpha: float, cap: int = 100000) -> int:
    """CP 반폭 ≤ target 되는 최소 iid 아이템 수(p 근방)."""
    n = 1
    while n <= cap:
        k = round(p * n)
        if _cp_half_width(k, n, alpha) <= half_width_target:
            return n
        n += 1
    raise ValueError("precision 목표가 cap 내 도달 불가 — target을 키우세요")


def collection_target(
    objective: str, *, n_current: int = 3, alpha: float = 0.05,
    target_power: float = 0.8, half_width_target: float | None = None,
    mean_cluster_size: float = 5.0, icc: float = 0.1,
    baseline: float | None = None, effect: float | None = None,
    n_sim: int = 1000, seed: int = 0, max_clusters: int = 40,
) -> CollectionTarget:
    """objective별 필요 회의수. n_additional=max(0, n_required-n_current).

    'comparison_floor' → comparison_floor_n(alpha)  (폐형식·가정 0)
    'comparison_power' → floor부터 power≥target 최소 n (sim, icc·baseline·effect 가정)
    'ci_precision'     → CP 반폭≤target 최소 n_iid를 DEFF 팽창(icc 민감도 필수 병기)
    """
    deff = 1.0 + (mean_cluster_size - 1.0) * icc

    if objective == "comparison_floor":
        n_req = comparison_floor_n(alpha)
        return CollectionTarget(
            objective, n_req, max(0, n_req - n_current), deff, None,
            detail={"note": "폐형식 ⌈log₂(2/α)⌉ — 가정 0, 데이터 독립"})

    if objective == "comparison_power":
        if baseline is None or effect is None:
            raise ValueError("comparison_power엔 baseline·effect 필요")
        n = comparison_floor_n(alpha)
        reached = False
        while n <= max_clusters:
            pr = simulate_paired_power(
                n_clusters=n, baseline=baseline, effect=effect,
                flags_per_cluster=round(mean_cluster_size), icc=icc,
                alpha=alpha, n_sim=n_sim, seed=seed)
            if pr.power >= target_power:
                reached = True
                break
            n += 1
        if not reached:
            # 조용히 시뮬한 적 없는 max_clusters+1을 반환하지 않는다 — _n_iid_for_precision과 동일 fail-loud.
            raise ValueError(
                f"comparison_power: target_power={target_power}가 n≤{max_clusters} 내 도달 불가 "
                f"(baseline={baseline}, effect={effect}, icc={icc}) — "
                "max_clusters를 키우거나 effect/target 재조정")
        return CollectionTarget(
            objective, n, max(0, n - n_current), deff, icc,
            detail={"icc_assumed": icc, "baseline": baseline, "effect": effect,
                    "warn": "icc·effect 가정 조건부 — 민감도 병기 권장"})

    if objective == "ci_precision":
        if half_width_target is None:
            raise ValueError("ci_precision엔 half_width_target 필요")
        p = 0.5 if baseline is None else baseline
        n_iid = _n_iid_for_precision(half_width_target, p, alpha)
        m = int(mean_cluster_size)
        sensitivity = {}
        for rho in (0.05, 0.1, 0.2):
            d = 1.0 + (mean_cluster_size - 1.0) * rho
            sensitivity[rho] = math.ceil(n_iid * d / mean_cluster_size)
        n_req = math.ceil(n_iid * deff / mean_cluster_size)
        return CollectionTarget(
            objective, n_req, max(0, n_req - n_current), deff, icc,
            detail={"n_iid": n_iid, "p_assumed": p, "mean_cluster_size": mean_cluster_size,
                    "icc_sensitivity": sensitivity, "m_int": m,
                    "note": "icc는 사전등록 가정 — 추정 불가, 민감도 grid 병기"})

    raise ValueError(f"objective∈{{comparison_floor,comparison_power,ci_precision}} — got {objective!r}")

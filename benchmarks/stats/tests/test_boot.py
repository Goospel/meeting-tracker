"""회의 수준 재샘플 CI + pooled Clopper-Pearson.

정직성 핵심: n=3에서 cluster bootstrap은 '판정용 타당 구간'이 아니라 '조악함을 스스로
폭로하는 진단'이다 — n_distinct_resamples(=10)와 granular 경고를 강제 노출. BCa는 소 cluster에서
무성 붕괴하므로 강등/퇴화로 막는다. pooled CP는 '정직한 CI'가 아니라 '폭의 낙관적 하한'.
"""

import math

import pytest

from bench_stats.boot import cluster_bootstrap_ci, pooled_proportion_ci
from bench_stats.types import ClusterBinary

# 현재 실측 3회의 (감지 recall): luma 4/4, greenmart 5/6, payments 4/5.
REAL = [ClusterBinary("luma", 4, 4), ClusterBinary("greenmart", 5, 6), ClusterBinary("payments", 4, 5)]


# ── cluster bootstrap (전열거 결정적) ────────────────────────────────────────
def test_bootstrap_n3_granular_grid():
    ci = cluster_bootstrap_ci(REAL, estimand="meeting_weighted")
    assert ci.method == "exact_enum_percentile"
    assert ci.n_distinct_resamples == 10          # C(2*3-1, 3) = 10
    assert any("granular" in w for w in ci.warnings)


def test_bootstrap_deterministic_seed_invariant():
    a = cluster_bootstrap_ci(REAL, estimand="meeting_weighted", seed=0)
    b = cluster_bootstrap_ci(REAL, estimand="meeting_weighted", seed=999)
    # 전열거는 RNG를 안 타므로 seed와 무관하게 비트동일.
    assert (a.lower, a.upper, a.point) == (b.lower, b.upper, b.point)


def test_bootstrap_hand_checked_percentile():
    # ratios [1,1,0]: 27 전열거 평균 분포 → nearest-rank 2.5%/97.5% = [0.0, 1.0].
    clusters = [ClusterBinary("a", 1, 1), ClusterBinary("b", 1, 1), ClusterBinary("c", 0, 1)]
    ci = cluster_bootstrap_ci(clusters, estimand="meeting_weighted")
    assert ci.point == pytest.approx(2 / 3)
    assert ci.lower == pytest.approx(0.0)
    assert ci.upper == pytest.approx(1.0)
    assert ci.n_distinct_resamples == 10


def test_bootstrap_bca_downgraded_at_n3():
    ci = cluster_bootstrap_ci(REAL, estimand="meeting_weighted", method="bca")
    assert ci.method == "degraded_percentile"
    assert any("bca" in w.lower() for w in ci.warnings)


def test_bootstrap_bca_pointmass_precision():
    # 전 클러스터 successes==n (예: FP=0 → precision point-mass) → BCa 미정의.
    pm = [ClusterBinary("m1", 3, 3), ClusterBinary("m2", 4, 4), ClusterBinary("m3", 5, 5)]
    ci = cluster_bootstrap_ci(pm, estimand="meeting_weighted", method="bca")
    assert ci.degenerate is True
    assert math.isnan(ci.lower) and math.isnan(ci.upper)
    assert any("point" in w.lower() or "mass" in w.lower() for w in ci.warnings)


def test_bootstrap_bca_finite_at_n12():
    # ≥10 cluster·비퇴화 → 실제 BCa 경로: 유한 구간, point 포함.
    clusters = [ClusterBinary(f"m{i}", s, 5) for i, s in enumerate([5, 4, 5, 3, 4, 5, 2, 4, 5, 3, 4, 5])]
    ci = cluster_bootstrap_ci(clusters, estimand="meeting_weighted", method="bca")
    assert ci.method == "bca"
    assert not ci.degenerate
    assert 0.0 <= ci.lower <= ci.point <= ci.upper <= 1.0
    assert not any("granular" in w for w in ci.warnings)   # 12 cluster는 격자 조밀


# ── pooled Clopper-Pearson (폭의 낙관적 하한) ────────────────────────────────
def test_pooled_flag_weighted():
    ci = pooled_proportion_ci(REAL, estimand="flag_weighted")
    assert ci.point == pytest.approx(13 / 15)              # 0.8667
    assert ci.lower == pytest.approx(0.5954, abs=5e-4)
    assert ci.upper == pytest.approx(0.9834, abs=5e-4)
    assert any("optimistic" in w or "clustering" in w for w in ci.warnings)


def test_pooled_meeting_weighted_point():
    ci = pooled_proportion_ci(REAL, estimand="meeting_weighted")
    assert ci.point == pytest.approx((1.0 + 5 / 6 + 4 / 5) / 3)  # 0.8778
    # 구간은 여전히 pooled 카운트 CP(폭 하한) — 경고 필수.
    assert any("optimistic" in w or "clustering" in w for w in ci.warnings)


def test_pooled_skips_empty_cluster():
    mixed = REAL + [ClusterBinary("empty", 0, 0)]
    ci = pooled_proportion_ci(mixed, estimand="flag_weighted")
    assert ci.point == pytest.approx(13 / 15)             # n=0은 분모에서 스킵
    assert any("n=0" in w or "empty" in w or "빈" in w for w in ci.warnings)


def test_pooled_fail_loud():
    with pytest.raises(ValueError):
        pooled_proportion_ci([])
    with pytest.raises(ValueError):
        pooled_proportion_ci([ClusterBinary("z", 0, 0)])  # 전부 n=0 → 분모 0

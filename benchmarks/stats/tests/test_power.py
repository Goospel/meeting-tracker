"""검정력·MDE·수집목표.

데이터 독립인 mde_exact_binomial / comparison_floor 기반 수집목표만이 n=3에서 통계적으로
타당한 전향 산출. 쌍체 검정력은 floor_p>alpha면 effect 무관 0으로 강제된다.
"""

import pytest

from bench_stats.power import (
    collection_target,
    mde_exact_binomial,
    mde_paired,
    simulate_paired_power,
)


# ── 쌍체 MDE / 검정력 ────────────────────────────────────────────────────────
def test_mde_paired_n3_alpha_unreachable():
    m = mde_paired(n_clusters=3, baseline=0.8, flags_per_cluster=5, icc=0.1,
                   effect_grid=[0.05, 0.1, 0.15, 0.2], n_sim=200, seed=0)
    assert m.mde is None
    assert m.reason == "alpha_unreachable_at_n"


def test_mde_paired_n8_finite_and_deterministic():
    kw = dict(n_clusters=8, baseline=0.5, flags_per_cluster=6, icc=0.05,
              effect_grid=[0.2, 0.3, 0.4, 0.5], n_sim=300, seed=1)
    a = mde_paired(**kw)
    b = mde_paired(**kw)
    assert a.mde == b.mde                      # seed 결정적
    assert a.mde is not None                    # 큰 effect·8회의면 검출 가능


def test_simulate_power_floor_forces_zero():
    # n=3 → floor_p=0.25>0.05 → 어떤 effect든 power=0.0.
    r = simulate_paired_power(n_clusters=3, baseline=0.5, effect=0.5,
                              flags_per_cluster=5, icc=0.1, n_sim=200, seed=0)
    assert r.power == 0.0
    assert r.reachable is False


def test_simulate_power_deterministic():
    kw = dict(n_clusters=7, baseline=0.5, effect=0.4, flags_per_cluster=5,
              icc=0.1, n_sim=200, seed=3)
    assert simulate_paired_power(**kw).power == simulate_paired_power(**kw).power


# ── 데이터 독립 MDE (단일 감지기/CI 정밀) ────────────────────────────────────
def test_mde_exact_binomial_monotone_n():
    p0 = 0.8
    small = mde_exact_binomial(20, p0).mde
    big = mde_exact_binomial(40, p0).mde
    assert big < small                          # n↑ → mde↓


def test_mde_exact_binomial_monotone_deff():
    p0 = 0.8
    lo = mde_exact_binomial(40, p0, deff=1.0).mde
    hi = mde_exact_binomial(40, p0, deff=2.0).mde
    assert hi > lo                              # deff↑ → n_eff↓ → mde↑


def test_mde_exact_binomial_n_eff():
    m = mde_exact_binomial(40, 0.8, deff=2.0)
    assert m.n_eff == 20                        # floor(40/2)


# ── 수집목표(중단규칙) ───────────────────────────────────────────────────────
def test_collection_comparison_floor():
    ct = collection_target("comparison_floor", n_current=3, alpha=0.05)
    assert ct.n_required == 6
    assert ct.n_additional == 3


def test_collection_ci_precision_icc_extremes():
    m_bar = 5.0
    ct0 = collection_target("ci_precision", n_current=3, half_width_target=0.1,
                            mean_cluster_size=m_bar, icc=0.0, baseline=0.5)
    n_iid = ct0.detail["n_iid"]
    assert ct0.n_required == -(-n_iid // int(m_bar))    # icc=0 → ceil(n_iid/m̄)
    ct1 = collection_target("ci_precision", n_current=3, half_width_target=0.1,
                            mean_cluster_size=m_bar, icc=1.0, baseline=0.5)
    assert ct1.deff == pytest.approx(m_bar)             # icc=1 → deff=m̄
    assert ct1.n_required == pytest.approx(n_iid, abs=1)  # meetings≈n_iid
    # icc 민감도 grid 필수 병기.
    assert set(ct0.detail["icc_sensitivity"]) >= {0.05, 0.1, 0.2}


def test_collection_ci_precision_requires_target():
    with pytest.raises(ValueError):
        collection_target("ci_precision", half_width_target=None)

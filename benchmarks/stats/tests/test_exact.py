"""수치 코어 회귀 — 정규화 불완전베타 / Clopper-Pearson / zero-event 상한 / phi_inv.

known-answer(교과서·독립검산)와 역전 일관성(구현 무관 보장)을 함께 건다:
- 항등식: I_x(1,1)=x, I_0.5(a,a)=0.5, I_0=0, I_1=1
- 역전 일관성: CP 하한 L에서 P(X≥k|L)=α/2, 상한 U에서 P(X≤k|U)=α/2 (이항 CDF 재계산)
"""

from math import comb

import pytest

from bench_stats.exact import (
    clopper_pearson,
    phi_inv,
    regularized_incomplete_beta,
    zero_event_upper_bound,
)


def _binom_ge(k, n, p):
    """P(X≥k | X~Bin(n,p)) — 구현과 독립인 정직 재계산(역전 일관성 검증용)."""
    return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))


def _binom_le(k, n, p):
    return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(0, k + 1))


# ── regularized_incomplete_beta 항등식 ──────────────────────────────────────
@pytest.mark.parametrize("x", [0.0, 0.13, 0.5, 0.87, 1.0])
def test_betainc_identity_a1_b1(x):
    # I_x(1,1) == x
    assert regularized_incomplete_beta(x, 1.0, 1.0) == pytest.approx(x, abs=1e-12)


@pytest.mark.parametrize("a", [2.0, 5.0, 13.0])
def test_betainc_symmetric_half(a):
    # I_0.5(a,a) == 0.5
    assert regularized_incomplete_beta(0.5, a, a) == pytest.approx(0.5, abs=1e-12)


def test_betainc_endpoints():
    assert regularized_incomplete_beta(0.0, 3.0, 4.0) == 0.0
    assert regularized_incomplete_beta(1.0, 3.0, 4.0) == 1.0


def test_betainc_domain_guard():
    with pytest.raises(ValueError):
        regularized_incomplete_beta(-0.01, 2.0, 2.0)
    with pytest.raises(ValueError):
        regularized_incomplete_beta(1.01, 2.0, 2.0)


# ── clopper_pearson known-answer + 퇴화 닫힌형 ───────────────────────────────
def test_cp_known_answer_13_15():
    lo, hi = clopper_pearson(13, 15)
    assert lo == pytest.approx(0.5954, abs=5e-4)
    assert hi == pytest.approx(0.9834, abs=5e-4)


def test_cp_degenerate_k_eq_n():
    # k=n → lower=(α/2)^(1/n), upper=1.0. precision '1.0'이 ≥0.75로 강등됨을 pin.
    lo, hi = clopper_pearson(13, 13)
    assert lo == pytest.approx(0.025 ** (1 / 13), abs=1e-9)  # ≈0.7530
    assert hi == 1.0


def test_cp_degenerate_k_zero():
    lo, hi = clopper_pearson(0, 10)
    assert lo == 0.0
    assert hi == pytest.approx(1 - 0.025 ** (1 / 10), abs=1e-9)  # ≈0.3085


def test_cp_k_eq_n_eq_1():
    lo, hi = clopper_pearson(1, 1)
    assert lo == pytest.approx(0.025, abs=1e-9)
    assert hi == 1.0


def test_cp_1_of_10():
    lo, hi = clopper_pearson(1, 10)
    assert lo == pytest.approx(0.00253, abs=1e-4)
    assert hi == pytest.approx(0.44502, abs=1e-4)


@pytest.mark.parametrize("k,n", [(13, 15), (2, 15), (1, 10), (7, 20)])
def test_cp_inversion_consistency(k, n):
    # 구현 무관 보장: 반환 하한/상한이 이항 CDF를 정확히 α/2로 만든다.
    lo, hi = clopper_pearson(k, n, alpha=0.05)
    if 0 < k < n:
        assert _binom_ge(k, n, lo) == pytest.approx(0.025, abs=1e-6)
        assert _binom_le(k, n, hi) == pytest.approx(0.025, abs=1e-6)


def test_cp_fail_loud():
    with pytest.raises(ValueError):
        clopper_pearson(0, 0)
    with pytest.raises(ValueError):
        clopper_pearson(11, 10)
    with pytest.raises(ValueError):
        clopper_pearson(3, 10, alpha=0.0)
    with pytest.raises(ValueError):
        clopper_pearson(3, 10, alpha=1.0)


# ── zero_event_upper_bound ──────────────────────────────────────────────────
def test_zero_event_bounds():
    r = zero_event_upper_bound(0, n_items=13, n_clusters=3, level=0.95)
    assert r["conservative_upper"] == pytest.approx(1 - 0.05 ** (1 / 3), abs=1e-6)  # ≈0.632
    assert r["optimistic_upper"] == pytest.approx(1 - 0.05 ** (1 / 13), abs=1e-6)  # ≈0.206
    assert r["reported"] == r["conservative_upper"]
    # 보수 상한이 낙관 상한보다 커야(더 큰 오류율 상한 = 더 겸손).
    assert r["conservative_upper"] > r["optimistic_upper"]
    # precision ≥ 1 - upper — '1.00' 미주장.
    assert 1 - r["conservative_upper"] == pytest.approx(0.368, abs=2e-3)


def test_zero_event_requires_zero():
    # 0-사건 전용 — k>0이면 오용이므로 거부.
    with pytest.raises(ValueError):
        zero_event_upper_bound(1, n_items=13, n_clusters=3)


# ── phi_inv (BCa 보조, n≥10 경로 전용) ──────────────────────────────────────
def test_phi_inv_known_answer():
    assert phi_inv(0.975) == pytest.approx(1.959963985, abs=1e-6)
    assert phi_inv(0.5) == pytest.approx(0.0, abs=1e-12)
    assert phi_inv(0.025) == pytest.approx(-1.959963985, abs=1e-6)


def test_phi_inv_domain():
    with pytest.raises(ValueError):
        phi_inv(0.0)
    with pytest.raises(ValueError):
        phi_inv(1.0)

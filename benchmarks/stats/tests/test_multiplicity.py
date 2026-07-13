"""다중비교 FWER 통제 — Holm-Bonferroni (step-down)."""

import pytest

from bench_stats.multiplicity import holm_adjust


def test_holm_known_answer():
    adj, rej = holm_adjust([0.01, 0.02, 0.03, 0.04], alpha=0.05)
    assert rej == (True, False, False, False)
    assert adj == pytest.approx((0.04, 0.06, 0.06, 0.06))


def test_holm_original_order_preserved():
    # 입력 순서가 뒤섞여도 반환은 입력 위치에 매핑.
    adj, rej = holm_adjust([0.04, 0.01, 0.03, 0.02], alpha=0.05)
    assert adj[1] == pytest.approx(0.04)   # 0.01이 가장 작음 → 4*0.01
    assert rej[1] is True
    assert rej == (False, True, False, False)


def test_holm_monotone_nondecreasing_sorted():
    adj, _ = holm_adjust([0.2, 0.001, 0.05, 0.01], alpha=0.05)
    s = sorted(adj)
    assert all(s[i] <= s[i + 1] + 1e-12 for i in range(len(s) - 1))


def test_holm_caps_at_one():
    adj, rej = holm_adjust([0.5, 0.6, 0.7], alpha=0.05)
    assert all(a <= 1.0 for a in adj)
    assert rej == (False, False, False)


def test_holm_empty():
    assert holm_adjust([]) == ((), ())

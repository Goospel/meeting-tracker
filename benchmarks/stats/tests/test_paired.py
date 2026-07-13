"""쌍체 검정 코어 — 정확 McNemar / 부호 floor / cluster 부호치환.

핵심 정직성: n=3 회의면 어떤 데이터가 나와도 양측 유의 최소 p=2/2³=0.25>0.05 →
구조적으로 유의 불가(UNDERPOWERED). 이걸 known-answer로 못박는다.
"""

import pytest

from bench_stats.paired import (
    comparison_floor_n,
    mcnemar_exact,
    min_attainable_two_sided_p,
    paired_cluster_permutation,
    sign_floor,
)
from bench_stats.types import PairedClusterBinary


# ── 정확 McNemar (flag 수준 부호검정) ────────────────────────────────────────
def test_mcnemar_known_answers():
    assert mcnemar_exact(8, 2).p_two_sided == pytest.approx(112 / 1024)
    assert mcnemar_exact(10, 0).p_two_sided == pytest.approx(2 / 1024)
    assert mcnemar_exact(9, 0).p_two_sided == pytest.approx(0.00390625)


def test_mcnemar_edges():
    assert mcnemar_exact(0, 0).p_two_sided == 1.0
    assert mcnemar_exact(1, 0).p_two_sided == 1.0


def test_mcnemar_symmetry():
    assert mcnemar_exact(8, 2).p_two_sided == mcnemar_exact(2, 8).p_two_sided
    assert mcnemar_exact(5, 1).p_two_sided == mcnemar_exact(1, 5).p_two_sided


def test_mcnemar_min_attainable_filled():
    r = mcnemar_exact(8, 2)
    assert r.min_attainable_p == pytest.approx(min_attainable_two_sided_p(10))
    assert r.n_discordant == 10


# ── 부호 floor / 비교 바닥 ───────────────────────────────────────────────────
def test_min_attainable_known():
    assert min_attainable_two_sided_p(3) == 0.25
    assert min_attainable_two_sided_p(6) == pytest.approx(2 / 64)
    assert min_attainable_two_sided_p(1) == 1.0
    assert min_attainable_two_sided_p(0) == 1.0


def test_comparison_floor_known():
    assert comparison_floor_n(0.05) == 6
    assert comparison_floor_n(0.01) == 8
    assert comparison_floor_n(0.10) == 5


def test_sign_floor_n3_unreachable():
    sf = sign_floor(3, alpha=0.05)
    assert sf.min_two_sided_p == 0.25
    assert sf.alpha_reachable is False
    assert sf.min_clusters_to_reach_alpha == 6


def test_sign_floor_n6_reachable():
    sf = sign_floor(6, alpha=0.05)
    assert sf.alpha_reachable is True


# ── cluster 부호치환 (판정용 1차 검정) ───────────────────────────────────────
def _all_a_superior(n):
    # 회의마다 A가 flag 1개 더 맞힘 — d_c=+1.
    return [PairedClusterBinary(f"m{i}", (True,), (False,)) for i in range(n)]


def test_permutation_n3_hits_floor():
    r = paired_cluster_permutation(_all_a_superior(3))
    assert r.p_two_sided == pytest.approx(0.25)      # 전열거 최소값 = floor
    assert r.min_attainable_p == 0.25
    assert "exact" in r.method


def test_permutation_deterministic():
    a = paired_cluster_permutation(_all_a_superior(3))
    b = paired_cluster_permutation(_all_a_superior(3))
    assert a.p_two_sided == b.p_two_sided            # 전열거 → seed 무관 결정적


def test_permutation_no_difference():
    # A=B 매 회의 → 관측 통계 0, p=1.0.
    same = [PairedClusterBinary(f"m{i}", (True, False), (True, False)) for i in range(4)]
    r = paired_cluster_permutation(same)
    assert r.p_two_sided == 1.0


def test_permutation_n6_can_beat_floor():
    r = paired_cluster_permutation(_all_a_superior(6))
    assert r.p_two_sided == pytest.approx(2 / 64)    # 6회의 전부 A우세 → 2/2^6
    assert r.min_attainable_p == pytest.approx(2 / 64)

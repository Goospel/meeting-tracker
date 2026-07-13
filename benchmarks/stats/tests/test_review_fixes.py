"""리뷰 회귀 테스트 — max-effort 코드리뷰(15건)에서 확인된 정확성 결함의 재발 방지.

각 테스트는 발견 번호(①~⑮)를 명시한다. 수정 전에는 전부 실패(Red)해야 하고,
수정 후 통과(Green)한다. 판정층의 존재 이유(극소 n 정직성)를 지키는 앵커.
"""

import math

import pytest

from bench_stats.boot import cluster_bootstrap_ci
from bench_stats.paired import cluster_sign_test
from bench_stats.power import (
    _simulate_paired_dataset,
    collection_target,
    mde_exact_binomial,
    simulate_paired_power,
)
from bench_stats.prereg import dump_prereg, freeze_prereg, load_prereg
from bench_stats.types import ClusterBinary
from bench_stats.verdict import verdict_paired, verdict_single

REAL = [ClusterBinary("luma", 4, 4), ClusterBinary("greenmart", 5, 6), ClusterBinary("payments", 4, 5)]


# ① verdict_paired: recall(성공/n)이 아닌 원 카운트 차를 비교 → 분모 다르면 오판정.
def test_paired_requires_equal_denominators():
    # B가 전 회의에서 엄밀히 우수(3/3=1.0 vs A 4/5=0.8)인데 카운트 차는 +1 → A 우세 오판정.
    a = [ClusterBinary(f"m{i}", 4, 5) for i in range(6)]
    b = [ClusterBinary(f"m{i}", 3, 3) for i in range(6)]
    with pytest.raises(ValueError):
        verdict_paired(a, b)


# ② verdict_single: 빈 회의(n=0)가 n_clusters를 부풀려 추론 플로어를 우회.
def test_single_empty_clusters_dont_grant_inference():
    padded = REAL + [ClusterBinary("e1", 0, 0), ClusterBinary("e2", 0, 0), ClusterBinary("e3", 0, 0)]
    v = verdict_single(padded, target=0.85)
    assert v.state == "DESCRIPTIVE_ONLY"     # 유효 회의 3개 → 플로어 미만
    assert v.n_clusters == 3                 # 빈 회의는 유효 표본에서 제외


# ③ verdict_single: 분산 0 파국이 FAILS보다 먼저 DEGENERATE로 삼켜짐.
def test_single_zero_variance_catastrophe_is_fails():
    zero = [ClusterBinary("a", 0, 5), ClusterBinary("b", 0, 5), ClusterBinary("c", 0, 5)]
    assert verdict_single(zero, target=0.85).state == "FAILS_TARGET"


# ④ verdict_single: k_cons=round(point*n)의 은행가반올림이 거짓 MEETS/FAILS 유발.
def test_single_no_false_meets_from_rounding():
    # point=5.5/6=0.9167; round(5.5)=6이 하한을 부풀려 거짓 MEETS.
    mc = [ClusterBinary("a", 5, 5), ClusterBinary("b", 5, 5), ClusterBinary("c", 5, 5),
          ClusterBinary("d", 5, 5), ClusterBinary("e", 5, 5), ClusterBinary("f", 1, 2)]
    assert verdict_single(mc, target=0.45).state != "MEETS_TARGET"


def test_single_no_false_fails_from_rounding():
    # point=2.5/6=0.4167; round(2.5)=2가 상한을 낮춰 거짓 FAILS.
    mc = [ClusterBinary("a", 5, 5), ClusterBinary("b", 5, 5), ClusterBinary("c", 1, 2),
          ClusterBinary("d", 0, 5), ClusterBinary("e", 0, 5), ClusterBinary("f", 0, 5)]
    assert verdict_single(mc, target=0.80).state != "FAILS_TARGET"


# ⑤ verdict_paired: 동점 회의(d=0)가 min_attainable_p의 n을 부풀려 플로어 게이트 왜곡.
def test_paired_ties_dont_inflate_floor():
    # d=[2,2,2,0,0,0] → 유효 3개 → 구조적 UNDERPOWERED(동점 3개가 도달성을 만들면 안 됨).
    a = [ClusterBinary(f"m{i}", s, 5) for i, s in enumerate([5, 5, 5, 3, 3, 3])]
    b = [ClusterBinary(f"m{i}", s, 5) for i, s in enumerate([3, 3, 3, 3, 3, 3])]
    assert verdict_paired(a, b).state == "UNDERPOWERED"


# ⑥ verdict_paired: 중복 cluster_id가 dict 컴프리헨션에서 조용히 마지막 값으로 덮임.
def test_paired_duplicate_id_fails_loud():
    a = [ClusterBinary("m1", 5, 5), ClusterBinary("m1", 0, 5), ClusterBinary("m2", 4, 5)]
    b = [ClusterBinary("m1", 3, 5), ClusterBinary("m2", 3, 5)]
    with pytest.raises(ValueError):
        verdict_paired(a, b)


# ⑦ cluster_bootstrap_ci: n=0 회의를 안 걸러 리샘플이 NaN을 CI 경계로 흘림.
def test_bootstrap_no_nan_with_empty_meeting_weighted():
    ci = cluster_bootstrap_ci([ClusterBinary("a", 0, 0), ClusterBinary("b", 1, 1)],
                              estimand="meeting_weighted")
    assert not math.isnan(ci.lower) and not math.isnan(ci.upper)


def test_bootstrap_no_nan_with_empty_flag_weighted():
    ci = cluster_bootstrap_ci(
        [ClusterBinary("a", 2, 2), ClusterBinary("b", 3, 3), ClusterBinary("c", 0, 0)],
        estimand="flag_weighted")
    assert not math.isnan(ci.lower) and not math.isnan(ci.upper)


def test_bootstrap_all_empty_fails_loud():
    with pytest.raises(ValueError):
        cluster_bootstrap_ci([ClusterBinary("a", 0, 0)], estimand="meeting_weighted")


# ⑧ power.py: icc 생성모형이 상관을 icc가 아니라 icc²로 실현(한 자릿수 과소).
def test_icc_realizes_target_correlation():
    import random

    rng = random.Random(0)
    icc = 0.4
    n = 20000
    xs = [_simulate_paired_dataset(rng, 1, 0.5, 0.5, 2, icc)[0] for _ in range(n)]
    x0 = [int(c.a_correct[0]) for c in xs]
    x1 = [int(c.a_correct[1]) for c in xs]
    m0, m1 = sum(x0) / n, sum(x1) / n
    cov = sum((a - m0) * (b - m1) for a, b in zip(x0, x1)) / n
    v0 = sum((a - m0) ** 2 for a in x0) / n
    v1 = sum((b - m1) ** 2 for b in x1) / n
    corr = cov / (v0 * v1) ** 0.5
    assert corr == pytest.approx(icc, abs=0.03)   # 이전엔 icc²=0.16으로 붕괴


# ⑨ collection_target(comparison_power): 도달 불가 시 시뮬 안 한 n=cap+1을 조용히 반환.
def test_comparison_power_unreachable_fails_loud():
    with pytest.raises(ValueError):
        collection_target("comparison_power", n_current=3, baseline=0.9, effect=0.005,
                          target_power=0.99, alpha=0.05, n_sim=80, seed=0, max_clusters=8)


# ⑩ simulate_paired_power: baseline+effect>1을 조용히 클리핑하고 명목 effect를 라벨.
def test_simulate_power_rejects_effect_beyond_one():
    with pytest.raises(ValueError):
        simulate_paired_power(n_clusters=6, baseline=0.8, effect=0.5,
                              flags_per_cluster=5, icc=0.1, n_sim=50, seed=0)


# ⑪ cluster_sign_test MC 경로: p=ge/n_mc에 +1 보정 없어 무효한 p=0 가능.
def test_mc_permutation_pvalue_never_zero():
    d = [1] * 21   # n>max_exact(20) → MC 경로
    r = cluster_sign_test(d, max_exact_clusters=20, n_mc=50, seed=0)
    assert r.p_two_sided > 0.0
    assert r.p_two_sided >= 1.0 / (50 + 1) - 1e-12
    assert "mc" in r.method


# ⑫ mde_exact_binomial: n_eff=int(n//deff)의 float 바닥나눗셈 off-by-one.
def test_mde_n_eff_robust_float_floor():
    # int(11//1.1)=9였으나 참 floor(11/1.1)=10.
    assert mde_exact_binomial(11, 0.8, deff=1.1).n_eff == 10


# ⑬ cluster_bootstrap_ci: level 미검증 → 퇴화 구간(또는 phi_inv 예외).
def test_bootstrap_rejects_invalid_level():
    with pytest.raises(ValueError):
        cluster_bootstrap_ci(REAL, level=0.0)
    with pytest.raises(ValueError):
        cluster_bootstrap_ci(REAL, level=1.0)


# ⑭ prereg: freeze는 raw tuple, load는 JSON list → .data 라운드트립 불일치.
def test_prereg_tuple_field_roundtrips(tmp_path):
    cfg = freeze_prereg(groups=("x", "y"), target=0.85)
    p = tmp_path / "pr.json"
    dump_prereg(cfg, p)
    loaded = load_prereg(p)
    assert loaded.data == cfg.data


# ⑮ ClusterBinary: 정수성 미검증 → 소수 카운트 허용(계약 위반).
@pytest.mark.parametrize("s,n", [(2.5, 5), (2, 5.0)])
def test_cluster_binary_rejects_non_integer(s, n):
    with pytest.raises((TypeError, ValueError)):
        ClusterBinary("bad", s, n)

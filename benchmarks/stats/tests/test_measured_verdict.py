"""실측 3회의를 통계 판정층에 관통 — '정직한 판정'을 실행 가능하게 동결.

detect_bench measurements/README.md의 종합표(실 Claude Opus 4.8, 골든 3건)를 통계층 입력으로
환원해, 이 층이 지금 데이터에 대해 낼 판정을 박제한다:
  recall per-meeting: luma 4/4, greenmart 5/6, payments 4/5 (TP/골든).
  precision: FP 0 / 13 예측 → '1.00' 미주장.

핵심: 3회의는 '얼마나 좋은지'는 못 말하고(DESCRIPTIVE_ONLY), '재앙은 아닌지'와 '얼마나 더
모아야 하는지(+3)'만 정직하게 말한다. 이 파일은 그 경계를 회귀로 고정한다.
"""

import pytest

from bench_stats.exact import zero_event_upper_bound
from bench_stats.paired import sign_floor
from bench_stats.power import mde_paired
from bench_stats.types import ClusterBinary
from bench_stats.verdict import verdict_paired, verdict_single

# 단일 출처 = detect_bench/measurements/README.md 종합표.
RECALL = [ClusterBinary("luma", 4, 4), ClusterBinary("greenmart", 5, 6), ClusterBinary("payments", 4, 5)]
N_PRED, N_FP = 13, 0   # 예측 13건, 가짜(FP) 0건 → 정밀도 1.00(관측), 그러나 미주장.


def test_recall_verdict_is_descriptive_only():
    v = verdict_single(RECALL, target=0.85, estimand="meeting_weighted")
    assert v.state == "DESCRIPTIVE_ONLY"                     # 목표 판정 봉쇄
    assert v.point == pytest.approx(0.877778, abs=1e-5)      # 회의가중
    assert v.detail["point_flag_weighted"] == pytest.approx(13 / 15, abs=1e-6)
    # pooled CP는 '폭의 낙관적 하한'으로만 — 값 자체는 [0.595,0.983].
    lo, hi = v.detail["pooled_cp_width_floor"]
    assert (lo, hi) == pytest.approx((0.5954, 0.9834), abs=5e-4)
    assert any("낙관적 하한" in w or "clustering" in w for w in v.warnings)


def test_collection_target_is_plus_three():
    v = verdict_single(RECALL, target=0.85)
    assert v.collection_target.n_required == 6
    assert v.collection_target.n_additional == 3            # 지금 +3부터 쌍체 유의 원리적 가능


def test_precision_not_claimed_one():
    # FP=0을 정밀도 1.00으로 못박지 않는다 — zero-event 보수 상한으로 강등.
    z = zero_event_upper_bound(N_FP, n_items=N_PRED, n_clusters=3)
    assert z["reported"] == z["conservative_upper"]
    assert 1 - z["conservative_upper"] == pytest.approx(0.368, abs=2e-3)   # precision≥0.368(보수)
    assert 1 - z["optimistic_upper"] == pytest.approx(0.794, abs=2e-3)     # precision≥0.794(낙관)


def test_sign_floor_blocks_significance():
    sf = sign_floor(3, alpha=0.05)
    assert sf.min_two_sided_p == 0.25
    assert sf.alpha_reachable is False
    assert sf.min_clusters_to_reach_alpha == 6


def test_paired_inert_with_single_detector():
    # 비교할 2번째 감지기 없음 → 쌍체 판정 전부 inert.
    v = verdict_paired(RECALL, [])
    assert v.state == "INSUFFICIENT_DATA"


def test_mde_paired_alpha_unreachable():
    m = mde_paired(n_clusters=3, baseline=0.8, flags_per_cluster=5, icc=0.1,
                   effect_grid=[0.05, 0.1, 0.2], n_sim=100, seed=0)
    assert m.mde is None
    assert m.reason == "alpha_unreachable_at_n"

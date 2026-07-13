"""판정 상태기계 — 극소 n에서 과대주장을 fail-loud로 거부하는 게 핵심.

보수방향 비대칭: 파국(FAILS_TARGET)은 cluster-보수 상한으로 floor 미만에서도 검출,
성공(MEETS_TARGET)은 정직 하한이라 floor(=6) 이상에서만 도달.
"""

import pytest

from bench_stats.types import ClusterBinary
from bench_stats.verdict import verdict_paired, verdict_per_type, verdict_single

REAL = [ClusterBinary("luma", 4, 4), ClusterBinary("greenmart", 5, 6), ClusterBinary("payments", 4, 5)]


def test_single_current_data_descriptive_only():
    v = verdict_single(REAL, target=0.85, estimand="meeting_weighted")
    assert v.state == "DESCRIPTIVE_ONLY"
    assert v.point == pytest.approx((1.0 + 5 / 6 + 4 / 5) / 3, abs=1e-6)  # 0.878
    assert v.collection_target is not None and v.collection_target.n_additional == 3


def test_single_current_data_flag_weighted_point():
    v = verdict_single(REAL, target=0.85, estimand="flag_weighted")
    assert v.point == pytest.approx(13 / 15, abs=1e-6)   # 0.867


def test_single_catastrophe_fails_below_floor():
    # recall 2/15 — cluster-보수 상한<0.85 → FAILS_TARGET (floor 미만에서도 도달).
    bad = [ClusterBinary("a", 1, 4), ClusterBinary("b", 1, 6), ClusterBinary("c", 0, 5)]
    v = verdict_single(bad, target=0.85, estimand="meeting_weighted")
    assert v.state == "FAILS_TARGET"


def test_single_meets_blocked_below_floor():
    # 완벽에 가까워도 n<floor면 MEETS 금지 — DESCRIPTIVE_ONLY 또는 그 이상, 단 MEETS_TARGET 아님.
    good = [ClusterBinary("a", 4, 4), ClusterBinary("b", 5, 6), ClusterBinary("c", 5, 5)]
    v = verdict_single(good, target=0.80)
    assert v.state != "MEETS_TARGET"


def test_single_gate_transition_at_floor():
    six = [ClusterBinary(f"m{i}", s, n) for i, (s, n) in
           enumerate([(4, 4), (5, 6), (4, 5), (4, 4), (5, 6), (4, 5)])]
    v = verdict_single(six, target=0.85, estimand="meeting_weighted")
    assert v.state != "DESCRIPTIVE_ONLY"           # n=6 → 추론 licence
    assert v.n_clusters == 6


def test_single_degenerate_all_equal():
    same = [ClusterBinary("a", 5, 5), ClusterBinary("b", 4, 4), ClusterBinary("c", 3, 3)]
    v = verdict_single(same, target=0.85)
    assert v.state == "DEGENERATE"


def test_single_insufficient_one_cluster():
    v = verdict_single([ClusterBinary("only", 4, 5)], target=0.85)
    assert v.state == "INSUFFICIENT_DATA"


def test_single_all_empty_degenerate():
    v = verdict_single([ClusterBinary("a", 0, 0), ClusterBinary("b", 0, 0)], target=0.85)
    assert v.state == "DEGENERATE"


# ── 쌍체 판정 ────────────────────────────────────────────────────────────────
def test_paired_n3_always_underpowered():
    a = [ClusterBinary(f"m{i}", s, 5) for i, s in enumerate([5, 5, 4])]
    b = [ClusterBinary(f"m{i}", s, 5) for i, s in enumerate([3, 2, 2])]
    v = verdict_paired(a, b)
    assert v.state == "UNDERPOWERED"
    assert v.collection_target.n_additional == 3


def test_paired_n6_significant():
    a = [ClusterBinary(f"m{i}", 4, 5) for i in range(6)]
    b = [ClusterBinary(f"m{i}", 2, 5) for i in range(6)]
    v = verdict_paired(a, b)
    assert v.state == "SIGNIFICANT"


def test_paired_no_second_detector_insufficient():
    v = verdict_paired(REAL, [])
    assert v.state == "INSUFFICIENT_DATA"


# ── per-type ─────────────────────────────────────────────────────────────────
def test_per_type_all_descriptive_at_n3():
    per_type = {
        "모순": [ClusterBinary("m1", 1, 1), ClusterBinary("m2", 0, 1), ClusterBinary("m3", 1, 1)],
        "번복": [ClusterBinary("m1", 1, 2), ClusterBinary("m2", 1, 1), ClusterBinary("m3", 1, 1)],
        "미해결": [ClusterBinary("m1", 1, 1), ClusterBinary("m2", 1, 2), ClusterBinary("m3", 0, 1)],
        "재논의": [ClusterBinary("m1", 1, 1), ClusterBinary("m2", 1, 1), ClusterBinary("m3", 1, 1)],
    }
    verdicts = verdict_per_type(per_type, target=0.85)
    assert set(verdicts) == set(per_type)
    assert all(v.state in ("DESCRIPTIVE_ONLY", "DEGENERATE", "INSUFFICIENT_DATA")
               for v in verdicts.values())

"""입력·결과 타입 — fail-loud 생성 가드."""

import pytest

from bench_stats.types import ClusterBinary, PairedClusterBinary


def test_cluster_binary_ok():
    cb = ClusterBinary("m1", 4, 4)
    assert (cb.cluster_id, cb.successes, cb.n) == ("m1", 4, 4)
    assert cb.ratio == 1.0


def test_cluster_binary_ratio_zero_n():
    # n=0은 허용(빈 클러스터)하되 ratio는 정의 불가 → None (조용한 0.0 아님).
    cb = ClusterBinary("empty", 0, 0)
    assert cb.ratio is None


@pytest.mark.parametrize("s,n", [(5, 4), (-1, 3), (3, -1)])
def test_cluster_binary_fail_loud(s, n):
    with pytest.raises(ValueError):
        ClusterBinary("bad", s, n)


def test_paired_ok():
    p = PairedClusterBinary("m1", (True, False, True), (True, False, False))
    assert p.a_hits == 2 and p.b_hits == 1
    assert p.net_discordance == 1  # a - b


def test_paired_unequal_length():
    with pytest.raises(ValueError):
        PairedClusterBinary("m1", (True, False), (True,))


def test_paired_precision_category_error():
    # precision은 골든 앵커가 없어 쌍 단위 구성 불가 — 생성 시점 차단.
    with pytest.raises(ValueError):
        PairedClusterBinary("m1", (True,), (False,), metric="precision")

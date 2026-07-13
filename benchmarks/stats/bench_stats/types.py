"""입력·결과 값 타입 — 감지·STT 공유 원자 + 모든 산출 dataclass.

핵심 입력 원자는 ClusterBinary('클러스터 안에 군집된 이진 결과')다. 두 벤치가 각자
지표를 (successes, n) per-cluster로 환원해 같은 층에 먹인다:
  - 감지 recall: successes=hit(TP), n=골든 flag수(TP+FN)  (지표 추출은 호출자 책임)
  - STT       : successes=클린토큰(n-sub-deleted), n=클립별 치명토큰수

모든 결과 dataclass는 frozen이고 warnings/method/estimand 같은 **정직성 라벨**을 품는다 —
point/CI를 라벨 없이 단독 인용하지 못하게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClusterBinary:
    """클러스터(회의/클립) 1개의 이진 집계. successes/n은 정수, 0≤successes≤n."""

    cluster_id: str
    successes: int
    n: int

    def __post_init__(self):
        if self.n < 0:
            raise ValueError(f"n≥0 필요 — {self.cluster_id}: n={self.n}")
        if self.successes < 0 or self.successes > self.n:
            raise ValueError(
                f"0≤successes≤n 필요 — {self.cluster_id}: successes={self.successes}, n={self.n}"
            )

    @property
    def ratio(self) -> float | None:
        """클러스터 내 비율. n=0이면 정의 불가 → None (조용한 0.0 금지)."""
        return self.successes / self.n if self.n else None


@dataclass(frozen=True)
class PairedClusterBinary:
    """두 감지기/두 config를 '같은 골든 앵커 flag별'로 정렬한 쌍체 단위.

    a_correct[j]/b_correct[j] = 회의 내 j번째 골든 flag를 A/B가 맞혔는가. 두 벡터 길이 동일.
    범주 제약: 골든-앵커 지표(recall류)에만 유효 — precision의 FP는 앵커가 없어 쌍이 존재하지
    않으므로 metric='precision' 입력을 생성 시점에 거부한다(범주오류 차단).
    """

    cluster_id: str
    a_correct: tuple[bool, ...]
    b_correct: tuple[bool, ...]
    metric: str = "recall"

    def __post_init__(self):
        if self.metric != "recall":
            raise ValueError(
                f"쌍체 단위는 골든-앵커 지표(recall)만 — metric={self.metric!r}는 쌍이 정의되지 않음"
            )
        if len(self.a_correct) != len(self.b_correct):
            raise ValueError(
                f"a_correct/b_correct 길이 동일 필요 — {self.cluster_id}: "
                f"{len(self.a_correct)} vs {len(self.b_correct)}"
            )
        object.__setattr__(self, "a_correct", tuple(bool(x) for x in self.a_correct))
        object.__setattr__(self, "b_correct", tuple(bool(x) for x in self.b_correct))

    @property
    def a_hits(self) -> int:
        return sum(self.a_correct)

    @property
    def b_hits(self) -> int:
        return sum(self.b_correct)

    @property
    def net_discordance(self) -> int:
        """회의 수준 부호통계 기여 = (A 맞힌 수) - (B 맞힌 수)."""
        return self.a_hits - self.b_hits


# ── 결과 타입 (전부 frozen, 정직성 라벨 포함) ────────────────────────────────
@dataclass(frozen=True)
class ExactCI:
    point: float
    lower: float
    upper: float
    level: float
    method: str
    estimand: str
    n_clusters: int
    n_items: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class BootstrapCI:
    point: float
    lower: float
    upper: float
    level: float
    method: str
    n_boot: int
    n_distinct_resamples: int
    degenerate: bool
    seed: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class McNemarResult:
    b: int
    c: int
    n_discordant: int
    p_two_sided: float
    method: str
    min_attainable_p: float
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SignFloor:
    n_clusters: int
    min_two_sided_p: float
    alpha: float
    alpha_reachable: bool
    min_clusters_to_reach_alpha: int


@dataclass(frozen=True)
class PowerResult:
    effect: float
    power: float
    floor_p: float
    reachable: bool
    seed: int
    n_clusters: int
    alpha: float


@dataclass(frozen=True)
class MDE:
    mde: float | None
    reason: str
    seed: int
    grid: tuple[float, ...] = ()
    n_eff: int | None = None
    k_crit: int | None = None
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CollectionTarget:
    objective: str
    n_required: int
    n_additional: int
    deff: float
    icc_assumed: float | None
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Verdict:
    state: str
    metric: str
    point: float | None
    estimand: str
    n_clusters: int
    n_items: int
    detail: dict = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    collection_target: CollectionTarget | None = None

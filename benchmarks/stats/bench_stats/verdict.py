"""판정 상태기계 — point/CI를 라벨 없이 내지 않고, 극소 n에서 과대주장을 fail-loud로 거부.

verdict_single: DEGENERATE / INSUFFICIENT_DATA / DESCRIPTIVE_ONLY / IMPRECISE_ESTIMATE /
                MEETS_TARGET / FAILS_TARGET / INCONCLUSIVE_VS_TARGET
verdict_paired: INSUFFICIENT_DATA / UNDERPOWERED / INCONCLUSIVE / SIGNIFICANT (+collection_target)
verdict_per_type: 타입별 verdict_single; n<floor면 전부 DESCRIPTIVE_ONLY.

보수방향 원칙: FAILS는 cluster-보수 상한(n_eff=n_clusters)<target이면 floor 미만에서도 도달(파국
검출은 floor-robust). MEETS는 정직 하한이 anticonservative측이라 floor(=6) 이상에서만 허용.
"""

from __future__ import annotations

import math
from statistics import fmean
from typing import Sequence

from .exact import clopper_pearson
from .paired import cluster_sign_test, min_attainable_two_sided_p
from .power import collection_target
from .types import ClusterBinary, Verdict

_EPS = 1e-12


def _point(nonempty, estimand):
    if estimand == "meeting_weighted":
        return fmean([c.successes / c.n for c in nonempty])
    if estimand == "flag_weighted":
        return sum(c.successes for c in nonempty) / sum(c.n for c in nonempty)
    raise ValueError(f"estimand∈{{meeting_weighted,flag_weighted}} — got {estimand!r}")


def verdict_single(
    clusters: Sequence[ClusterBinary], *, target: float,
    precision_target: float | None = None, alpha: float = 0.05,
    inference_floor: int = 6, estimand: str = "meeting_weighted",
    metric: str = "recall",
) -> Verdict:
    """단일 감지기 지표 판정.

    유효 표본은 정보 있는 회의(n>0)뿐 — 빈 회의(n=0)는 지표에 기여하지 않으므로 추론
    관측수(플로어 게이트·보수 CP)에서 제외한다. 보고 n_clusters도 유효 회의 수로 한다.
    """
    n_total = len(clusters)
    nonempty = [c for c in clusters if c.n > 0]
    n_infer = len(nonempty)             # 유효(정보 있는) 회의 수 — 모든 추론 결정의 근거
    n_items = sum(c.n for c in clusters)

    base_warnings: tuple[str, ...] = ()
    if n_total > n_infer:
        base_warnings = (
            f"빈 회의(n=0) {n_total - n_infer}개 추론에서 제외 — 유효 표본은 {n_infer}개 회의",)

    def mk(state, point, detail=None, ct=None, warnings=()):
        return Verdict(state=state, metric=metric, point=point, estimand=estimand,
                       n_clusters=n_infer, n_items=n_items, detail=detail or {},
                       warnings=base_warnings + tuple(warnings), collection_target=ct)

    if n_items == 0 or not nonempty:
        return mk("DEGENERATE", None, {"reason": "지표 미정의 — 아이템 0"})
    if n_infer < 2:
        ct = collection_target("comparison_floor", n_current=n_infer, alpha=alpha)
        return mk("INSUFFICIENT_DATA", _point(nonempty, estimand),
                  {"reason": "회의간 spread 관측 불가(n_clusters<2)"}, ct)

    ratios = [c.successes / c.n for c in nonempty]
    point = _point(nonempty, estimand)
    # cluster-보수 CP: 점추정을 n_infer 유효관측으로 못박은 겸손한 구간.
    # round(은행가반올림)는 .5 tie에서 하한을 부풀리거나(거짓 MEETS) 상한을 낮춰(거짓 FAILS)
    # 보수성을 깬다. 비-tie는 최근접(기존과 동일 — 파국 민감도 보존), tie만 하한↓·상한↑로 가른다.
    prod = point * n_infer
    k_lo = max(0, min(n_infer, math.ceil(prod - 0.5 - 1e-9)))    # round-half-down
    k_hi = max(0, min(n_infer, math.floor(prod + 0.5 + 1e-9)))   # round-half-up
    lo_cons = clopper_pearson(k_lo, n_infer, alpha=alpha)[0]
    hi_cons = clopper_pearson(k_hi, n_infer, alpha=alpha)[1]
    sk, sn = sum(c.successes for c in nonempty), sum(c.n for c in nonempty)
    lo_pool, hi_pool = clopper_pearson(sk, sn, alpha=alpha)
    detail = {
        "point_meeting_weighted": fmean(ratios),
        "point_flag_weighted": sk / sn,
        "cluster_conservative_cp": (lo_cons, hi_cons),
        "pooled_cp_width_floor": (lo_pool, hi_pool),
        "sign_floor_p": min_attainable_two_sided_p(n_infer),
        "inference_floor": inference_floor,
        "target": target,
    }
    warnings = ("pooled CP는 clustering 무시 — '폭의 낙관적 하한'(참 구간 더 넓음)",)

    # 파국(floor-robust): 가장 겸손한 상한으로도 target 미달 — 분산 0이어도 먼저 검출.
    if hi_cons < target:
        ct = collection_target("comparison_floor", n_current=n_infer, alpha=alpha)
        return mk("FAILS_TARGET", point, {**detail, "reason": "cluster-보수 상한<target"},
                  ct, warnings)

    # 분산 0(무변동): 파국이 아님이 확정된 뒤에만 DEGENERATE.
    if max(ratios) - min(ratios) < _EPS:
        return mk("DEGENERATE", ratios[0],
                  {**detail, "reason": "회의간 분산 0 — 무변동, 구간 무의미"}, None, warnings)

    # floor 미만: 목표 달성 주장 봉쇄.
    if n_infer < inference_floor:
        ct = collection_target("comparison_floor", n_current=n_infer, alpha=alpha)
        return mk("DESCRIPTIVE_ONLY", point,
                  {**detail, "reason": f"n_clusters<inference_floor({inference_floor}) — 목표 판정 봉쇄"},
                  ct, warnings)

    # n≥floor: 정직 하한 기반 판정.
    if lo_cons >= target:
        return mk("MEETS_TARGET", point, detail, None, warnings)
    if precision_target is not None and (hi_cons - lo_cons) / 2.0 > precision_target:
        ct = collection_target("ci_precision", n_current=n_infer, alpha=alpha,
                               half_width_target=precision_target, baseline=point)
        return mk("IMPRECISE_ESTIMATE", point,
                  {**detail, "reason": "CI 반폭>precision_target"}, ct, warnings)
    return mk("INCONCLUSIVE_VS_TARGET", point,
              {**detail, "reason": "충분한 n인데 CI가 target을 걸침"}, None, warnings)


def verdict_paired(
    clusters_a: Sequence[ClusterBinary], clusters_b: Sequence[ClusterBinary], *,
    alpha: float = 0.05, inference_floor: int = 6, metric: str = "recall",
) -> Verdict:
    """두 감지기 쌍체 판정 — 회의 id로 정렬해 회의별 차이(A-B)에 cluster 부호치환.

    n<comparison_floor면 관측 무관 UNDERPOWERED(+수집목표). floor 이상에서만 SIGNIFICANT 가능.

    회의별 카운트 차(A맞힘-B맞힘)가 net discordance이려면 두 감지기가 **같은 골든 앵커**로
    채점됐어야 한다(recall 분모=골든 flag수 동일). 따라서 공통 회의는 n_a==n_b를 요구하고,
    중복 cluster_id는 조용한 데이터 손실이라 생성 시점에 fail-loud로 거부한다. 동점(d=0)
    회의는 부호검정에 정보가 0이므로 구조적 도달성(min_attainable_p)의 n에서 제외한다.
    """
    ids_a = [c.cluster_id for c in clusters_a]
    ids_b = [c.cluster_id for c in clusters_b]
    if len(set(ids_a)) != len(ids_a):
        raise ValueError(f"clusters_a에 중복 cluster_id — 쌍체 정렬 불가: {ids_a}")
    if len(set(ids_b)) != len(ids_b):
        raise ValueError(f"clusters_b에 중복 cluster_id — 쌍체 정렬 불가: {ids_b}")
    bya = {c.cluster_id: c for c in clusters_a}
    byb = {c.cluster_id: c for c in clusters_b}
    common = [cid for cid in bya if cid in byb]

    def mk(state, detail=None, ct=None):
        return Verdict(state=state, metric=f"{metric}(paired)", point=None,
                       estimand="paired_net_discordance", n_clusters=len(common),
                       n_items=sum(bya[c].n for c in common), detail=detail or {},
                       collection_target=ct)

    if not common:
        return mk("INSUFFICIENT_DATA", {"reason": "공통 회의 없음 — 비교 대상 부재(감지기 1개?)"})
    mismatched = [cid for cid in common if bya[cid].n != byb[cid].n]
    if mismatched:
        raise ValueError(
            "쌍체 비교는 회의별 골든 앵커 수 일치 필요(n_a≠n_b) — 카운트 차가 recall 차와 "
            f"어긋남: {[(cid, bya[cid].n, byb[cid].n) for cid in mismatched[:5]]}")
    d = [bya[cid].successes - byb[cid].successes for cid in common]
    n_informative = sum(1 for x in d if x != 0)   # 동점은 부호검정에 정보 0
    floor_p = min_attainable_two_sided_p(n_informative)
    if floor_p > alpha:
        ct = collection_target("comparison_floor", n_current=n_informative, alpha=alpha)
        return mk("UNDERPOWERED",
                  {"reason": f"min_attainable_p={floor_p}>alpha — 구조적 유의 불가",
                   "floor_p": floor_p, "n_informative": n_informative, "net_diffs": d}, ct)
    r = cluster_sign_test(d)
    detail = {"p_two_sided": r.p_two_sided, "b_clusters": r.b, "c_clusters": r.c,
              "method": r.method, "n_informative": n_informative, "net_diffs": d}
    if r.p_two_sided <= alpha:
        return mk("SIGNIFICANT", detail)
    return mk("INCONCLUSIVE", detail)


def verdict_per_type(
    per_type: dict, *, target: float, alpha: float = 0.05, method: str = "holm",
    inference_floor: int = 6, estimand: str = "meeting_weighted",
) -> dict:
    """타입별 verdict_single. n<floor면 각 타입이 DESCRIPTIVE_ONLY/DEGENERATE/INSUFFICIENT_DATA.

    추론(n≥floor) 타입이 생기면 target 비교 p값에 Holm을 적용해야 하나, v1의 극소 n에서는
    전부 서술적이라 다중비교가 무의미(가족 통제는 홀드아웃 수집 후).
    """
    return {
        t: verdict_single(cs, target=target, alpha=alpha,
                          inference_floor=inference_floor, estimand=estimand, metric=t)
        for t, cs in per_type.items()
    }

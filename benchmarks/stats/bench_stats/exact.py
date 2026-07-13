"""정확(exact) 수치 코어 — 점근근사 없이 극소표본에 정직한 구간·상한.

- regularized_incomplete_beta: Clopper-Pearson·정확이항 CDF의 수치 심장(Lentz 연속분수).
- clopper_pearson: 이항 비율 k/n의 정확(보수적) 양측 CI. 베타 분위 역변환은 단조 이분법으로.
- zero_event_upper_bound: 0-사건(예 FP=0)을 1.0으로 못박지 않기 위한 단측 정확 상한(낙관/보수 병기).
- phi_inv: 표준정규 역CDF(Acklam) — BCa 편향보정 보조. n≥10 경로에서만 쓰인다.

전부 stdlib(math)만. 결정적.
"""

from __future__ import annotations

import math

_FPMIN = 1e-300


def _betacf(x: float, a: float, b: float) -> float:
    """I_x(a,b) 연속분수(Numerical Recipes betacf). Lentz 방법, ~1e-15 수렴."""
    maxit, eps = 300, 3e-16
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, maxit + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) <= eps:
            break
    return h


def regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    """정규화 불완전베타 I_x(a,b) ∈ [0,1]. 항등식: I_x(1,1)=x, I_0.5(a,a)=0.5, I_0=0, I_1=1."""
    if x < 0.0 or x > 1.0:
        raise ValueError(f"x는 [0,1] — got {x}")
    if a <= 0.0 or b <= 0.0:
        raise ValueError("a,b>0 필요")
    if x == 0.0:
        return 0.0
    if x == 1.0:
        return 1.0
    ln_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(a * math.log(x) + b * math.log1p(-x) + ln_beta)
    # 수렴 빠른 쪽 가지 선택 후 대칭식으로 보정.
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(x, a, b) / a
    return 1.0 - front * _betacf(1.0 - x, b, a) / b


def _beta_ppf(q: float, a: float, b: float) -> float:
    """I_x(a,b)=q 를 만족하는 x — 단조 이분법(구현 무관·bulletproof)."""
    if q <= 0.0:
        return 0.0
    if q >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(200):  # 2^-200 → 기계정밀 훨씬 이하
        mid = (lo + hi) / 2.0
        if regularized_incomplete_beta(mid, a, b) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """이항 비율 k/n의 정확(보수적) 양측 CI, 신뢰수준 1-alpha.

    하한 L: I_L(k, n-k+1)=alpha/2  ⇔  P(X≥k | p=L)=alpha/2  (k=0이면 0.0)
    상한 U: I_U(k+1, n-k)=1-alpha/2 ⇔ P(X≤k | p=U)=alpha/2  (k=n이면 1.0)
    퇴화 닫힌형: k=0→(0, 1-(α/2)^(1/n)); k=n→((α/2)^(1/n), 1).
    """
    if n <= 0:
        raise ValueError(f"n>0 필요 — got n={n} (조용한 0.0 금지)")
    if k < 0 or k > n:
        raise ValueError(f"0≤k≤n 필요 — got k={k}, n={n}")
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha∈(0,1) — got {alpha}")
    a2 = alpha / 2.0
    lower = 0.0 if k == 0 else _beta_ppf(a2, k, n - k + 1)
    upper = 1.0 if k == n else _beta_ppf(1.0 - a2, k + 1, n - k)
    return lower, upper


def zero_event_upper_bound(k: int, n_items: int, n_clusters: int, *, level: float = 0.95) -> dict:
    """0-사건 오류율의 단측 정확 상한. 낙관(n_eff=n_items)·보수(n_eff=n_clusters) 병기.

    '사건 0건'을 '확률 0'으로 못박지 않는다(예: FP=0 → 정밀도 1.00 미주장).
    upper(n_eff) = 1 - (1-level)^(1/n_eff)  (단측). '보고값'은 보수(=clustering 존중).
    design_effect는 소 cluster로 추정 불가하므로 데이터로 고르지 않고 두 극단을 함께 노출한다.
    """
    if k != 0:
        raise ValueError(f"zero_event_upper_bound는 k=0 전용 — got k={k}")
    if n_items <= 0 or n_clusters <= 0:
        raise ValueError("n_items>0, n_clusters>0 필요")
    if not (0.0 < level < 1.0):
        raise ValueError(f"level∈(0,1) — got {level}")
    tail = 1.0 - level
    optimistic = 1.0 - tail ** (1.0 / n_items)
    conservative = 1.0 - tail ** (1.0 / n_clusters)
    return {
        "optimistic_upper": optimistic,
        "conservative_upper": conservative,
        "rule_of_three": 3.0 / n_clusters,
        "reported": conservative,
    }


# Acklam 유리근사 계수 (표준정규 역CDF, |오차|~1.15e-9).
_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
      3.754408661907416e+00)
_PLOW, _PHIGH = 0.02425, 1.0 - 0.02425


def phi_inv(p: float) -> float:
    """표준정규 역CDF Φ⁻¹(p). Acklam 근사 → 1 Halley 스텝(erf)으로 정련해 배정밀 근접.

    known-answer: Φ⁻¹(0.975)=1.959963985, Φ⁻¹(0.5)=0. BCa 편향보정 보조(n≥10 경로 전용).
    """
    if not (0.0 < p < 1.0):
        raise ValueError(f"p∈(0,1) — got {p}")
    if p < _PLOW:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
            ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    elif p <= _PHIGH:
        q = p - 0.5
        r = q * q
        x = (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / \
            (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
            ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    # Halley 정련 1스텝: Φ(x)-p 를 erf로 정확 평가.
    e = 0.5 * math.erfc(-x / math.sqrt(2.0)) - p
    u = e * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
    x = x - u / (1.0 + x * u / 2.0)
    return x

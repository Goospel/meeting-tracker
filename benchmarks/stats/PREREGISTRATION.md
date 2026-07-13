# 사전등록 (Pre-registration) — 감지 품질 통계 판정

> **이 문서는 홀드아웃 회의를 수집하기 *전에* 분석계획을 못박는다.** 목적은 사후 선택편향(손잡이를
> 데이터 보고 나서 유리하게 돌리는 것) 차단. 필드는 `bench_stats.prereg.freeze_prereg(...)`로 동결해
> 콘텐츠 해시를 커밋하고, 판정 산출물이 그 해시를 참조한다.

## ⚠️ 현재 3건의 지위 — 탐색적/서술적

`detect_bench/measurements/`의 luma·greenmart·payments 3회의는 **이미 관측됐다**. 따라서 이들은
**탐색적(exploratory)·서술적**으로만 취급하며, 아래 엄격한 사전등록은 **앞으로 수집할 홀드아웃
회의에만** 적용된다. 3건에 사전등록을 소급 적용하는 것은 사후등록(HARKing)이므로 금지한다.

## 1. 1차 종점(primary endpoint)과 estimand

- **지표**: 감지 **재현율(recall)** — 흐름단절을 놓치는 것(FN)이 운영상 가장 비싼 오류.
- **estimand**: **`meeting_weighted`** — 회의별 recall의 **비가중 평균**. 회의를 분석단위로 삼아
  유효표본=회의수 원칙과 정합(큰 회의가 KPI를 지배하지 않음).
  - 회의 크기가 불균등하면 `flag_weighted`(Σhit/Σ골든)와 값이 **다르다**(3건 관측: 0.878 vs 0.867).
    이 불일치 때문에 estimand를 필수로 고정한다.
- k/n 추출: `successes`=type-strict 매칭 TP, `n`=회의별 골든 flag 수(TP+FN). (채점은 `detect_bench`.)

## 2. 검정

- **1차 검정**: **cluster 부호치환**(`paired_cluster_permutation`) — 교환단위=회의. clustering을
  존중(flag 수준 McNemar는 anticonservative라 서술·앵커 전용).
- 양측(two-sided), **α = 0.05**, 연속성보정 없음(정확이항이라 불요).

## 3. 목표(target)와 판정 임계

- **재현율 목표 = 0.85** (사전등록 상수 — 홀드아웃 언블라인딩 전 amendment 라벨로만 변경).
- 판정: `verdict_single(..., target=0.85, estimand="meeting_weighted", inference_floor=6)`.
- **inference_floor = 6** (= `comparison_floor_n(0.05)`). 단일 감지기 `DESCRIPTIVE_ONLY` 탈출 게이트도 6로 통일.

## 4. 신뢰구간

- **1차 CI**: Clopper-Pearson(정확). level 0.95.
- cluster bootstrap은 **진단**(n≤6 전열거로 결정적, BCa는 ≥10 cluster에서만). `granular` 경고 강제.
- pooled CP는 clustering 무시 → "폭의 낙관적 하한"으로만 라벨. 단독 인용 금지.
- **정밀도(FP=0)**: `zero_event_upper_bound`로 상한 보고 — "precision=1.00"을 못박지 않는다.

## 5. 검정력 / MDE 가정 (가정임 명시)

- 생성모형: baseline(관측 근사), 회의당 flag 분포(관측 m̄≈5), **ICC(ρ)** = 사전등록 가정.
- `target_power = 0.8`, seed 고정 몬테카를로, `effect_grid` 사전 지정.
- **ICC 민감도 필수 병기**: ρ ∈ {0.05, 0.10, 0.20}. 단일 ρ의 n_required를 정밀로 읽지 않는다.
- 구조적 floor: `min_attainable_two_sided_p(n) > α`면 모든 effect에서 power=0 → MDE 부존재.

## 6. 다중비교 가족

- 1차 가족 = **overall 재현율 1개**. per-type(모순/번복/미해결/재논의)은 **보조**이며 각 분모가
  회의당 1~2로 희소 → 현재 n에서 전부 `DESCRIPTIVE_ONLY`.
- per-type을 추론에 쓸 경우 **Holm-Bonferroni**(`holm_adjust`)로 FWER를 가족 α=0.05로 통제.
- STT CTER은 **별개 가족**으로 사전등록(같은 `ClusterBinary` 구조 재사용).

## 7. 중단규칙 (수집목표)

`collection_target(...)`으로 목표별 필요 회의 수:

| 목표 | 공식 | 현재(+n) |
|---|---|---|
| 쌍체 유의 원리적 가능 | `comparison_floor_n(0.05)` = 6 | **+3** |
| 쌍체 80% 검정력 | sim 상향탐색(ρ 가정 조건부) | ≥ +3 (ρ 민감도) |
| 단일 CI 반폭 ≤ 0.10 | 정확이항 n_iid → DEFF=1+(m̄−1)ρ 팽창 | ρ 민감도 병기 |

## 8. 동결 절차

```python
from bench_stats.prereg import freeze_prereg, dump_prereg
cfg = freeze_prereg(
    primary_endpoint="recall", estimand="meeting_weighted",
    alpha=0.05, target=0.85, test="cluster_sign_permutation",
    inference_floor=6, ci="clopper_pearson", multiplicity="holm",
    target_power=0.8, icc_sensitivity=[0.05, 0.1, 0.2],
    scope="future_holdout_only", exploratory_meetings=["luma", "greenmart", "payments"],
)
dump_prereg(cfg, "PREREG_FROZEN.json")   # 콘텐츠 해시 커밋 — 사후 조정은 해시 불일치로 폭로
```

언블라인딩 후 변경은 **조용한 편집이 아니라 라벨된 amendment로 로깅**한다. 기술적 해시는 감지일
뿐, 최종 방어는 **커밋 이력·리뷰**다.

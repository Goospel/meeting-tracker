# 통계 판정층 (`bench_stats`)

> 감지·STT 벤치마크 **공유** 층. 극소표본에서 CI·검정·MDE를 **정직하게** — "얼마나 좋은가"를
> 판정하기 전에 "판정할 수 있는가"를 먼저 판정한다.

## 핵심 입장 — 유효표본은 아이템이 아니라 클러스터다

감지 flag(또는 STT 치명토큰)는 **회의(클립) 안에 군집**돼 독립이 아니다. 그래서 통계적 유효
표본은 flag 15개가 아니라 **회의 3개**다. 이 층의 임무는 둘:

1. **거부** — 극소 n에서 방어 불가능한 주장을 fail-loud로 막는다 (프로젝트의 "데이터 정직" DNA).
2. **답** — 데이터 독립 공식으로 "얼마나 더 모아야 말할 수 있는가"를 사전등록해 답한다.

**킬러 정직성 함수** (관측 무관·가정 0·폐형식):

| 함수 | 값 | 의미 |
|---|---|---|
| `min_attainable_two_sided_p(n)` = 2/2ⁿ | n=3 → **0.25** | 부호치환이 낼 수 있는 **최소** 양측 p |
| `comparison_floor_n(α)` = ⌈log₂(2/α)⌉ | α=0.05 → **6** | 쌍체 유의가 원리적으로 가능해지는 최소 회의 수 |

n=3이면 **어떤 데이터가 나와도** 쌍체 유의 최소 p=0.25 > 0.05 → 구조적으로 유의 불가.

## 모듈

| 파일 | 역할 |
|---|---|
| `types.py` | `ClusterBinary(cluster_id, successes, n)` 공유 입력 원자 + 모든 결과 dataclass(정직성 라벨 포함) |
| `exact.py` | 정규화 불완전베타 · Clopper-Pearson 정확 CI · zero-event 상한 · Φ⁻¹ |
| `boot.py` | 회의 수준 cluster bootstrap(n≤6 전열거로 결정적, BCa 게이트/강등) · pooled CP |
| `paired.py` | 정확 McNemar · 부호 floor · **cluster 부호치환(판정용 1차 검정)** |
| `power.py` | 정확이항 MDE · 쌍체 검정력 시뮬 · **수집목표(중단규칙)** |
| `multiplicity.py` | Holm-Bonferroni (per-type 가족 FWER) |
| `verdict.py` | **판정 상태기계** — 단일/쌍체/per-type |
| `prereg.py` | 사전등록 동결(정렬키 JSON + 해시 — 사후 손잡이 조정 폭로) |

두 벤치가 각자 지표를 `(successes, n)` per-cluster로 환원해 같은 층에 먹인다:
- **감지 recall**: `successes`=hit(TP), `n`=골든 flag수(TP+FN)
- **STT**: `successes`=클린토큰(n−sub−deleted), `n`=클립별 치명토큰수

## 판정 상태기계

| 상태 | 조건 | 뜻 |
|---|---|---|
| `DEGENERATE` | 아이템 0 / 회의간 분산 0 | 지표 무의미 |
| `INSUFFICIENT_DATA` | n_clusters<2 / 비교대상 부재 | 서술조차 불가 |
| `DESCRIPTIVE_ONLY` | 2≤n<floor(6) | 점추정만 — 목표 달성/미달 주장 봉쇄 |
| `IMPRECISE_ESTIMATE` | n≥floor·구간 과다 | 추론 licence됐으나 결정 불가 |
| `MEETS_TARGET` | n≥floor·정직 하한≥target | 목표 충족(floor 미만 도달 금지) |
| `FAILS_TARGET` | cluster-보수 상한<target | 파국 — **floor 미만에서도 검출** |
| `INCONCLUSIVE_VS_TARGET` | n≥floor·CI가 target 걸침 | 방향 미결 |
| `UNDERPOWERED` (쌍체) | min_attainable_p>α | 구조적 유의 불가(+수집목표) |
| `INCONCLUSIVE`/`SIGNIFICANT` (쌍체) | n≥floor·p>α / p≤α | 관측 차 유의 미달 / 확립 |

**보수방향 원칙**: 파국(FAILS)은 가장 겸손한 상한으로도 미달일 때라 floor 미만에서도 도달;
성공(MEETS)은 하한이 anticonservative측이라 floor 이상에서만 허용.

## 지금 3회의 데이터에 대한 정직한 판정

감지기 1개(Opus 4.8)를 회의 3건에 잰 실측(`detect_bench/measurements/`)을 이 층에 관통시키면
(`tests/test_measured_verdict.py`가 회귀로 고정):

| 산출 | 값 | 판정 |
|---|---|---|
| 재현율(회의가중) | 0.878 | **DESCRIPTIVE_ONLY** — 목표 판정 봉쇄(n=3<6) |
| pooled CP | [0.595, 0.983] | "정직한 CI 아님 — clustering 무시한 **폭의 낙관적 하한**" |
| 정밀도(FP 0/13) | ≥0.368(보수)·≥0.794(낙관) | **"1.00" 미주장** — zero-event 상한으로 강등 |
| 쌍체 비교 | — | **INSUFFICIENT_DATA** (감지기 1개뿐, 전 쌍체 machinery inert) |
| MDE(쌍체) | None | `alpha_unreachable_at_n` |
| 수집목표 | **+3회의** | 쌍체 유의가 원리적으로 가능해지는 지점(총 6) |

> 한 줄: 3회의는 "얼마나 좋은지"는 못 말하고, "재앙은 아닌지(파국 검출)"와 "얼마나 더 모아야
> 하는지(+3부터)"만 정직하게 말한다.

## 제약 · 결정성

- **런타임 의존성 0** — stdlib(`math·random·statistics·fractions·hashlib`)만. scipy/numpy 유입 0
  이라야 `detect_bench`·`stt_bench`의 zero-dep가 전이적으로 보존된다.
- **결정성(seed) ≠ 통계적 정밀** — cluster bootstrap은 n≤6에서 전열거라 seed 무관 결정적이지만,
  `n_distinct_resamples`(3회의→10)와 `granular` 경고를 강제 노출해 매끄러운 숫자가 정밀로
  오인되지 않게 한다.

## 재현 / 테스트

```bash
cd benchmarks/stats
python -m pytest -q          # 92 테스트 (known-answer·퇴화 엣지·상태기계·실측 판정 동결)
```

known-answer 앵커: `clopper_pearson(13,15)=[0.5954,0.9834]`, `mcnemar_exact(8,2)=112/1024`,
`min_attainable_two_sided_p(3)=0.25`, `comparison_floor_n(0.05)=6`, `Φ⁻¹(0.975)=1.959963985`,
Holm `[.01,.02,.03,.04]→(.04,.06,.06,.06)`.

사전등록 계획은 [`PREREGISTRATION.md`](PREREGISTRATION.md) — 홀드아웃 회의 수집 **전에** 동결.

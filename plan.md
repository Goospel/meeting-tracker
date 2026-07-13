# plan.md — 작업 계획 내비게이터

> **이 문서의 역할**: 앞으로 할 일을 *미리* 적어 두는 살아있는 계획서.
> "지금 어디까지 왔고, 다음에 뭘 하고, 무엇을 일부러 미뤘는가"를 한눈에.
>
> - 큰 그림(왜 이 순서인가)은 [`README.md` 구축 순서](README.md#구축-순서-mvp-우선)가 단일 출처. 여기서는 그걸 **체크박스 단위로 쪼갠 실행 계획**을 관리한다.
> - 완료한 일의 기록은 [`changeLog.md`](changeLog.md), 작업 중 만난 함정은 [`troubleshooting.md`](troubleshooting.md).
> - 상세 스펙은 [`docs/spec.md`](docs/spec.md), 데이터 계약은 [`docs/data-schema.json`](docs/data-schema.json).

**범례**: ✅ 완료 · 🔜 다음 차례 · ⬜ 예정 · ⏸ 의도적 보류(v2) · ⚠️ 리스크/전제

---

## 🎯 지금 상태 (2026-07-14)

**구축순서 1·2단계(STT·감지 측정 코어) + 공유 통계 판정층(ⓑ)까지 완료·머지.** 실측 3회의 종합 **정밀도 1.00·재현율 0.87**(탐색적 — n=3 < 통계 플로어 6, 판정=DESCRIPTIVE_ONLY, 수집목표 +3).
**3단계(파이프라인 통합) 착수** — **Python 우선 MVP**, 판단 패널 워크플로우로 5-PR 분해 확정. **PR-1(상태머신 순수 코어)·PR-2(영속화) 완료**(PR-1: 2차 리뷰 10종 반영 TDD 52 / PR-2: 적대 리뷰 5각 CONFIRMED 5종 반영 — 포트 대체가능성·빈값 술어·:memory: 가드 등, TDD 101), 다음은 PR-3(SttPort). 아래 [3단계](#3-파이프라인-통합--python-우선-mvp-착수) 참조.
(1단계 Track A 합성 골든셋 빌더/렌더 레이어 상세는 아래 1단계 — 남은 건 실제 비-네이버 TTS 렌더뿐, 크레덴셜 대기.)

---

## 구축 순서 — 6단계 (README 기준)

### 1. STT 골든셋 벤치마크 *(제품 성패 지점)*

> 한국어 회의의 숫자·고유명사 STT 정확도를 실측해, 모순감지의 입력 신뢰도를 먼저 검증한다.

- ✅ **측정 코어** — `benchmarks/stt/`, 런타임 의존성 0, TDD 110 테스트 (PR #4)
  - ✅ CTER(치명 토큰 값 등가 채점) — sub=가짜모순 / del=놓친모순 / ambiguous=needs_review 분리
  - ✅ 한국어 수·날짜·시각 파서 (Sino/고유어/소수/범위/단위)
  - ✅ 적대적 코드리뷰 2라운드 (F1~F13, R1~R15 회귀 고정)
- 🔜 **테스트 데이터 확보 (2트랙 · 녹음 불필요)** — 벤치마크의 병목은 오디오가 아니라 **신뢰 레퍼런스 전사(골든)**다. 조사 결과, 직접 녹음 없이 확보하는 최선은 2트랙 병행:
  - 🔜 **Track A (진행 중)** — 합성 **단일 소스 빌더 완료** (`stt_bench/synth.py`, PR #7): 마크업 스크립트 1개 → **골든 JSON + TTS 매니페스트** 파생. 오프셋 자동 계산(오프셋 불변식은 `validate_golden`이 실검사)·canonical은 파서 파생(→ 파서 오파싱은 게이트가 아닌 **회귀 테스트**가 방어). 오탈·불균형 마크업/통화 날조는 즉시 에러. 첫 골든 `synth_budget_reversal`(화자 3인·치명토큰 7·같은 화자 번복 2건) 커밋. **max-effort 코드리뷰 반영**(마크업 엄격화·통화 폴백 제거·TIME meridiem·CLI 매니페스트·문서 정직화), TDD 19테스트. **남은 것(진행 중 · 크레덴셜 없이 마무리)**:
    - ✅ **렌더 레이어 = `TtsPort` + stdlib 톤 렌더러 + 팩토리 확장점** (`render.py`, PR #8). 런타임 의존성 0 불변식이라 Azure/Google SDK는 못 싣는다 → 크레덴셜-불요 `ToneTtsPort`(화자별 사인 톤으로 파이프라인을 **실제로** 검증: 매니페스트→WAV 타임라인+렌더 리포트)를 실동작 구현으로 넣고, Azure/Google은 `get_port`가 `TtsCredentialError`로 명확히 막는 확장점만, `naver`는 벤더 편향으로 거부. 크레덴셜 오면 포트만 스왑.
    - ✅ **마크업 문법 확장** — PROPER_NOUN `aliases=`(축약 허용목록, 채점기 `allowed`가 소비)·`manual` 플래그(파서 미파생 canonical opt-out, 채점기가 ambiguous 처리)·`canonical=`(manual 전용). 채점기·검증기는 이미 지원 → **synth 마크업 방출만** 확장. 무의미 조합(aliases on 비-PROPER_NOUN, canonical= without manual, aliases+manual, 이중 key, 빈 값, 빈 surface)은 즉시 에러(무성 no-op 차단).
    - ✅ **적대적 리뷰 반영**(36에이전트 find→verify→sweep, 25 confirmed): 무성 실패·크래시 11종 수정(amplitude 포화, 빈 aliases/key, aliases+manual, 이중 key, report-out 부모 미생성, render_clip 에러 미포착, gap 음수, sample_rate=0, stderr utf-8, 빈 surface, 비-list 매니페스트). 회귀 13종 추가.
    - ✅ **추가 리뷰 반영**(`/code-review ultra` 로컬 max-effort): 필드 setter **가드 비대칭** 6종 — 중복 `aliases=`/`canonical=` 무성 last-wins, 빈 `canonical=` fallback, 선행 빈 필드 무명 key 오거부, `render.main` 매니페스트 읽기 트레이스백(→클린 `return 2`), README 수치. 뿌리 = 기존 setter(`_set_key`) 가드 미복제(→ `T-028`). 회귀 6종 추가 → 전체 **167 통과**.
    - ⏸ **보류(리뷰 defer)**: golden.py aliases NFC 게이트 검사(synth 출력은 이미 NFC·사전존재 갭) · manual 파서가능 타입 가드(의도적 opt-out) · get_port 확장점(합의된 설계) · 매니페스트 per-key KeyError(신뢰 내부 산출물).
    - ⏸ 잡음·리버브·전화코덱·겹말 증강 + 실제 비-네이버 TTS 렌더 → **크레덴셜 확보 시**. ⚠️ 벤치 대상에 클로바 포함 → 렌더는 **반드시 비-네이버**(같은 벤더 음향 prior 편향 회피).
  - ⬜ **Track B (병렬 신청)** — AI-Hub `dataSetSn=464` '주요영역별 회의 음성'(사람검수 전사 + 다자 + 화자라벨, RTZR 벤치의 그 데이터셋). 본인인증 + 활용신청 승인 필요, **비상업·재배포 금지**. ⚠️ 비식별화 마스킹(`*`)이 치명토큰 가릴 위험 → 샘플 JSON 실측 선행.
  - ⏸ **Track C (후순위)** — 국회 예결위·국감 속기록 + 영상회의록: 치명토큰 최상·실제 번복이나 **자구정정된 준-축어**라 구간별 치명토큰 수동 교정 필요. 하드케이스 보강용.
  - ⚠️ **공개 범위**: 오디오·전사는 라이선스상 로컬 전용 → repo엔 **CTER 수치 + 합성 생성 스크립트**만 공개.
- ✅ **통계 판정층** (ⓑ, `benchmarks/stats/` 공유 패키지, PR #15) — McNemar 정확·cluster bootstrap(BCa 게이트)·사전등록 MDE. 감지·STT 공유(`ClusterBinary`로 환원). 실제 추론 사용은 다중 회의 수집 후(홀드아웃).
- ⏸ **STT 어댑터** (v2) — `SttPort` 뒤 Clova/AWS Transcribe Live·Replay 러너 → **크레덴셜 필요**
- ⏸ **화자 귀속 지표** (v2) — critical_speaker_error, DER → 모순·번복은 "같은 사람"이 정의라 필수
- ⏸ **역할 스왑 / contradiction_key** (v2)
- ⏸ **프록시 실증** (v2) — STT 오류를 주입해 Claude 감지 영향 측정
- ⏸ **S7(hedge/flags 채점), S3(마지막 주 의미)** — 리뷰에서 v2로 분류
- ⚠️ **실측 전제**: STT(클로바/AWS) + TTS(Azure/Google) 크레덴셜. **오디오는 녹음 불필요**(위 2트랙) — 남은 전제는 크레덴셜뿐.

### 2. 분석 품질 검증 ✅ 채점 코어(`benchmarks/detection/`, PR #9) · ✅ 실측(골든 3건, Opus 4.8, PR #14)

> 완벽한 전사본을 입력으로 줬을 때 Claude 모순감지가 얼마나 맞히는가 — per-type precision/recall.
> stage-1 철학 계승: 순수·결정적·**런타임 의존성 0** 채점기 + TDD, mock으로 크레덴셜 없이 선행.

- ✅ **골든 라벨 스키마 + 로더/검증 게이트** (`labels.py`, PR #9) — flag 4종(모순/번복/미해결/재논의) + statements(quote·speaker) + 전사 세그먼트 양방향 역참조 일관성 게이트(무드리프트 방지). 골든 = `docs/data-schema.json`의 완성 회의 1건 재사용(전사 25세그·flag 4).
- ✅ **quote grounding 검증기** (`grounding.py`) — 예측 인용이 전사에 실재하는지 대조(NFC 부분일치 + 토큰 Jaccard≥0.6 폴백). **이중 역할**: 할루시 인용 드롭 + 예측 flag가 건드리는 세그먼트 해소(매칭 키).
- ✅ **감지 채점 하네스** (`score.py`) — 객체탐지식 **그리디 매칭**(같은 type + 세그먼트집합 Jaccard≥0.5로 1:1). 매칭=TP / 미매칭 골든=**놓친(FN)** / 미매칭 예측=**가짜(FP, ungrounded/unmatched 분리)**. per-type P/R/F1 + **localization(type-무관) 이중 채점**으로 라벨만 틀린 경우(type_confusion) 분리 노출.
- ✅ **리포트 + CLI** (`report.py`) — 회의 단위 마크다운(가짜/놓친/타입혼동). mock 예측(faithful→완벽·contaminated→4실패모드) 픽스처로 크레덴셜 없이 end-to-end.
- ✅ **적대적 리뷰 반영**(38에이전트, 25 confirmed): localization을 strict 확장으로 재구성(altitude), grounding 최밀착 선택, 역방향 게이트, 골든 grounding 가드 등 8종.
- ✅ **적대적 리뷰 2차 반영**(10앵글, 15 확정): 정타 속 할루시 인용 노출(`TaintedMatch`+🟠), 절삭 인용 가드, 동점 타이브레이크 내용 기준화(순서 의존 제거), `no_evidence` 분리, 타입혼동 FP 표기 일관화, bare `pytest` 수집, 순서-순열 불변 테스트. TDD **46테스트**.
- ✅ **실측 투입 전 필수 보강 3종**(PR #10, mock에선 안 밟히던 실측 전제): ① 인접 세그먼트 걸친 인용 → **전사 화자 동질성** 기반 span grounding(교차화자 스티칭 거부, 최소 커버 창 축약, 모호하면 grounding 안 함) ② 반복 발화 → statement speaker/time 힌트로 올바른 출현 귀속 ③ 변형 type/누락 키/비정형 shape → 신뢰 불가 예측을 필드·컨테이너별 per-flag 강등(골든은 엄격). 적대적 리뷰 3라운드(HIGH: 문자열 time_sec·null quote 크래시, mid-run 경계 인용 드롭 등 확정결함 수정), TDD.
- ✅ **적대적 리뷰 5R(xhigh: 10앵글→후보별 검증 16→갭 스윕) 반영**(PR #10, 16확정+스윕 2): **뿌리 수정** — 골든 grounding을 단일 세그먼트(main 의미론+힌트)로 되돌리고 span 확장은 예측 전용으로(골든 segset 팽창→정탐 FP+FN·기존 유효 골든 오거부 동시 해소). 힌트 산술 가드 완성(`_num` NaN 강등, `_pick` 비숫자 start_sec inf 정합, 창 거리=창 내 최근접), tier-2 동점 하이재킹(`any(cand in span)`), 공백 세그먼트 이중 공백, 빈 화자 스티칭 보수 거부, quote:null→no_evidence 분리, 골든 필드 엄격성(quote/speaker/time_sec/start_sec/메타), falsy id 0, 전량 파싱불가 클린 에러, `_safe` HTML 엔티티, 화자 NFC. TDD 107테스트(82→+25).
- ✅ **감지 어댑터 레이어(ⓐ)** (`detect.py`, PR #11) — 골든 전사 → 프롬프트(정답 누출 0) → 감지 포트 → 응답 파싱 → pred flags. Track A 렌더 레이어와 같은 패턴: `ReplayDetectorPort`(캔드 응답 재생 = 크레덴셜 0으로 전 파이프라인 실검증) + `ClaudeDetectorPort`(실제 Anthropic API를 **stdlib urllib**로 — 런타임 의존성 0 유지, `ANTHROPIC_API_KEY` 게이트). CLI가 pred JSON 산출 → 기존 `report --pred` 소비. **적대적 리뷰 3R 수렴**(2R: 파서 '첫 파싱값' 강탈 → 의미 기반 선택 `→ T-031`; 3R xhigh: 휴리스틱이 극단 카디널리티(0건·1건·절단)에서 역전 → 프롬프트 예시 **비파싱 표기**로 뿌리 수정 + flag스러운 원소 카운트·내용 우선·절단 클린 에러 + `stop_reason`/`timeout`/`--max-tokens`/게이트 단일화 `→ T-032·T-033`), TDD 168테스트. **남은 건 실제 API 실호출 실측뿐(크레덴셜 대기)** — 포트만 스왑.
- ✅ **골든 회의 2건째 — 하드케이스(ⓒ)** (`greenmart_meeting.json`, PR #12) — 이중 중첩(한 라인 2 flag)·반복발화 분해(byte-동일 디코이 vs 근거 time 갈림)·모순↔번복 근접(type_confusion)·교차화자 near-miss·같은 type 복수를 담은 26전사·6flag 정답. **judge panel**(5 seed 설계 → 3 심사 만장일치)로 선정, faithful/contaminated/리플레이로 어댑터 실경로 관통. 적대적 리뷰 1R(생존 10) 반영: **예측 time 관용 파싱**(`→ T-034`, 반복발화 판별의 numeric-단일신호 취약성)·f3 모순 라벨 정합·s14 미표기 미해결 FP 함정 해소·테스트 엄정성 5종. TDD 191테스트.
- ✅ **골든 회의 3건째 — 경계 span·tier2 하드케이스** (`payments_postmortem.json`, PR #13) — **인접 동일화자 세그먼트(STT 분할 모사)**로 골든 1·2엔 없던 **경계 span grounding·tier2 퍼지를 채점 경로에서** 스트레스(채점에서 span 타는 첫 골든). f1 첫 진술이 s6·s7에 쪼개진 경계 인용 → 예측은 인용 하나로 내 span이 `{s6,s7}` 회수, 골든은 세그먼트별로 라벨. 27전사·5flag. plan line 67의 **5R 보류 2건(① 모호성 비대칭 ② 경계 퍼지 부재)을 재설계 없이 현행 동작 pin**. 적대적 리뷰 1R(생존 2) 반영: **[1] tier2 load-bearing 교정**(f4를 3세그로 늘려 tier2가 채점에 판별적이게 — 2세그면 tier1 하나로 J=0.5 충족해 잉여였음)·[2] 임계·타이브레이크 비공허화·문면 정직성 3종·EOL 함정(`→ T-036`). TDD 216테스트.
- ✅ **실제 Claude API 실호출 실측 — 골든 3건**(`measurements/`, PR #14, Opus 4.8) — 어댑터(포트 스왑)로 3회의 전사를 실감지 → 채점. **종합 정밀도 1.00 · 재현율 0.87**(15 flag 중 TP 13 / FP 0 / FN 2). 세 회의 모두 가짜 감지·할루시 인용·타입 혼동 0. **놓친 2건은 전부 재현율이며 경계 중첩 과소감지**(번복에 흡수된 자기모순 / 미래 약속형 미해결) — 실패가 오탐이 아니라 미묘한 겹침 누락 쪽(제품상 유리한 실패 방향). 실 pred를 `measurements/`에 동결(비결정 LLM 출력 재현 불가) + `test_measured_real.py`로 채점 회귀 고정(실 API 재호출 없이 grounding/score 회귀 포착). TDD 229테스트.
- ✅ **통계 판정층(ⓑ)** — 신설 공유 패키지 `benchmarks/stats/`(`bench_stats`), PR #15. **유효표본=회의(cluster)** 원칙: 정확 Clopper-Pearson·cluster 부호치환(판정 1차 검정)·정확이항 MDE·수집목표·Holm·9-상태 판정기계·사전등록 동결. 킬러 정직성 `min_attainable_two_sided_p(n)=2/2ⁿ`·`comparison_floor_n(0.05)=6` → n=3은 관측 무관 유의 불가. 실측 3건 판정=**DESCRIPTIVE_ONLY**(정밀도 1.00 미주장, 수집목표 +3), zero-dep·TDD 92테스트. **실 추론 사용은 홀드아웃 수집 후**(현재 3건은 탐색적).
- ⚠️ **5R 보류 2건(설계 재작업 — 자체 리뷰 라운드 필요)**: ① 단일/스팬 경로의 모호성 정책 비대칭(단일=첫 출현 추측 vs 스팬=거부 — STT 분할 여부만으로 TP↔FP+FN 플립, 거부가 '할루시'로 오라벨) ② 경계 인용 퍼지 tier 부재(창 매칭이 verbatim 전용이라 한 단어 의역된 경계 인용이 전량 할루시 — 퍼지 창 tier는 과매칭 위험이 커 임계·중재 설계 필요). 둘 다 매칭 의미론 변경이라 실측 데이터 확보 후 재설계. **골든 3건째(PR #13)가 이 두 gap을 하드케이스로 스트레스·현행 동작 pin**(test_gap1/gap2) — 재설계 시 이 테스트들이 '알림' 역할.
- ⚠️ 전제: 실측은 Claude API 크레덴셜 — **측정 하네스·채점기 골격은 mock으로 선행**(이 단계).

### 3. 파이프라인 통합 — Python 우선 MVP 착수

> **순서 결정(확정, 2026-07-14)**: 파이프라인(3·4단계)과 인프라 스케일(5·6단계)에 **다른 게이트**를 건다.
> - **파이프라인(3·4)은 타당성 게이트로만** — 이미 실측 3회(정밀도 1.00·재현율 0.87)로 통과. 3·4의 임무는 품질 재증명이 아니라 (a) 업로드→STT→분석→저장이 상태머신으로 도는 **가장 싼 end-to-end 관통** + (b) 회의 3→6+건 **데이터 수집 엔진**.
> - **인프라 스케일(5·6)만 통계 유의 게이트(n≥6)** 뒤에 — 멀티테넌시·RLS·Terraform·ECS·SQS/DLQ·blue-green은 통계로 품질이 유의하게 확인되기 전엔 커밋 안 함.
> - **스택 = Python 우선**: 기존 `run_detection`·`get_detector`·`build_detection_prompt` 재사용(재구현 0). 프로덕션 Spring/RDS 전환은 stage 5에서 포트 뒤 어댑터 스왑(README 선언 유지, MVP만 Python 관통). 판단 패널 워크플로우(3 설계 → 적대 비평 → 종합)로 분해 확정.

새 top-level **`pipeline/`**(제품 코드 — `benchmarks/`는 측정 벤치). zero-dep·포트/리플레이·크레덴셜 가장자리 게이트·순수 결정적 코어·TDD DNA 계승. 영속화 = stdlib **sqlite3**(JobRepository 포트 뒤; Postgres/RLS는 stage 5). 포트: **JobRepository**(InMemory fake+Sqlite)·**SttPort**(Replay+Clova 게이트)·**DetectorPort**(기존 detect.py 재사용)·Clock/IdSource(순수 seam).

- ✅ **PR-1 상태머신 순수 코어** (`pipeline_core/state.py`) — State(UPLOADED→TRANSCRIBING→TRANSCRIBED→ANALYZING→ANALYZED→DONE + FAILED→PERMANENTLY_FAILED)·명시 전이표·불변식·멱등키·실패/재시도. **비동기 형상**(제출은 TRANSCRIBING 유지·콜백이 와야 TRANSCRIBED) 못박음 = 실 클로바 전환 시 오케스트레이터 전면 재작성 방지. **ANALYZED 중간 상태** = 정확히-한번 재개 seam. IO·포트·영속화 0. 루트 conftest 배선으로 detect_bench 재사용 고정. 적대적 리뷰 1차(계약 구멍 4종) + **2차 리뷰(`/code-review ultra` 폴백) 결함 10종** 반영 — F1 터미널 멱등 대칭(`_TERMINAL_MAKER`)·F2 상태 진입 불변식 균일 강제(`_STATE_REQUIRES`+`_check_state_invariant`, 손상 rehydrate가 빈 전사/결과 위 유료 재실행·정확히-한번 seam 관통 못하게)·`_ORDER` 도출·conftest shadowing 교정. 워크플로우 5축 적대 검증 SOUND. zero-dep·TDD **52**.
- ✅ **PR-2 영속화** (`pipeline_core/repository.py`) — `JobRepository`(InMemory fake + stdlib sqlite3)·재시작 복원(새 인스턴스가 같은 파일 재오픈)·낙관적동시성(version 컬럼)·`get_store` 팩토리(detect.get_detector 미러). 전사·결과 ref는 로우 TEXT 저장(별도 StoragePort·transitions 감사테이블 미도입 — 리더가 없어 write-only 死코드, PR1 RETRYING 뺀 것과 동일 YAGNI). **로드/저장 경계 전수검증** `state.validate_persisted`(단일 출처, save/get 대칭 호출): PR-1 `_check_state_invariant`가 피해경로 None만 막았으니, 빈 문자열('')·공백·비-str ref·상태별 전 ref 계보(ANALYZING의 transcript 보존)·필수 스칼라(id/audio_ref/attempts)까지 마감. **적대적 리뷰 5각→검증→스윕**(CONFIRMED 5 반영): ① 포트 대체가능성 — validate가 audio_ref/attempts NOT NULL 미검사 → InMemory 성공·Sqlite raw IntegrityError 발산을 균일 CorruptJob으로 ② 빈값 술어 str-only → `not isinstance(str) or not strip()` (공백만·BLOB b'' 관통 차단) ③ `:memory:` 연산별커넥션 비호환 → 생성자 거부 ④ `_COLUMNS` 死코드 제거 ⑤ INSERT 경합·락 경합 docstring 과장 제거(동시 라이터는 stage5 하드닝). zero-dep·TDD **101**. (advance/save '' 비대칭=문서화된 의도 분업, all_jobs 전체실패=PR4 복원정책 미결로 defer.)
- ⬜ **PR-3 SttPort + transcript→meeting 코어스 어댑터** — 리플레이가 **원시 STT 형상**(diarizer speaker_0/1·segment_id 없음·거친 세그먼트) 반환·정답 누출 0, 실 Clova 생성시점 게이트 확장점만. STT 산출을 strict 골든 로더 `meeting_from_data`로 **라우팅 안 함**.
- ⬜ **PR-4 오케스트레이터 + 단일사용자 CLI** — 워킹 스켈레톤. `python -m pipeline_core.run --stt replay --detector replay --db …` 한 방이 상태머신 구동→sqlite 영속→기존 `run_detection`→DONE. 전 구간 크레덴셜 0 관통.
- ⬜ **PR-5 실패/재시도/크래시복구 정확히-한번** — 실패 주입→FAILED·failed_from 재개(재-STT 회피)·재개 시 감지 **정확히 1회**(유료 이중호출 차단)·terminal + 적대적 리뷰 라운드. ⚠️ **주의(PR-1 F1 파생)**: `PERMANENTLY_FAILED+RETRY`는 이제 멱등 no-op(at-least-once 흡수). 오퍼레이터가 예산을 올려 **수동 재요청**하려면 RETRY 재전송이 아니라 **별도 이벤트(un-terminate/requeue)**를 도입해야 한다 — RETRY 재전송은 조용히 삼켜진다(터미널 정합).
- ⏸ **크레덴셜/실오디오 확보 후**: 실 Clova 실호출·웹훅 **HMAC/nonce 보안**·S3 실업로드 — HMAC 코어는 벤더 서명 스펙 확정 후(추측 구현은 오히려 재작성 리스크 제조).
- ⏸ **Spring 오케스트레이터 + RDS 전환** — stage 5(멀티테넌시)에서 포트 스왑. README 프로덕션 백엔드 선언은 유지.

### 4. 프론트 UX ⬜

- ⬜ 타임라인 리본
- ⬜ 상충 발언 비교 뷰 + grounding 하이라이트 + 원문 오디오 재생 링크
- ⬜ "연결된 그래프" 뷰 (React Flow / force-directed) — README 컨셉 섹션 참조

### 5. 멀티테넌시 + 인증 ⬜

- ⬜ Cognito(authN) + Postgres memberships(authZ)
- ⬜ RDS RLS 멀티테넌시 (org_id 기반 방어심층)
- ⬜ 테넌트별 비용 쿼터 + 업로드 시점 예상비용 게이트

### 6. 인프라 하드닝 ⬜

- ⬜ Terraform (AWS + NCP)
- ⬜ api/worker 분리 + SQS/DLQ
- ⬜ blue/green 배포, GitHub Actions OIDC

---

## 🧭 다음 착수 후보 (외부 전제 없이 가능한 것)

1. ✅ **문서 골격 정비** — plan / changeLog / troubleshooting ([PR #5](https://github.com/Goospel/meeting-tracker/pull/5), 머지)
2. ✅ **Track A 합성 골든셋 빌더 + 첫 골든** — 단일 소스 스크립트→골든+매니페스트, TDD (PR #7)
2b. ✅ **Track A 렌더 레이어 + 마크업 확장** — TtsPort+톤 렌더러+팩토리, aliases/manual 마크업, 적대적 리뷰 반영, TDD (PR #8). Track A는 이제 **실제 비-네이버 TTS 렌더만 크레덴셜 대기**로 종료.
3. ✅ **2단계 분석 채점 하네스 골격** — 골든 라벨·grounding·그리디 매칭·리포트, mock 예측으로 크레덴셜 없이 관통, TDD 46 (PR #9, `benchmarks/detection/`)
3b. ✅ **실측 전 필수 보강 3종** — 전사-화자 기반 span grounding·반복발화 힌트·신뢰불가 예측 shape 강등, 적대적 리뷰 4R 수렴, TDD 82 (PR #10)
3c. ✅ **적대적 리뷰 5R(xhigh) 반영** — 골든 단일 grounding 복원(span은 예측 전용)·힌트 산술 가드(NaN/start_sec/창 거리)·클린 에러 대칭·`_safe` HTML, TDD 107 (PR #10)
3d. ✅ **감지 어댑터 레이어(ⓐ)** — 골든 전사 → 프롬프트 → 감지 포트(리플레이/Claude, stdlib HTTP·크레덴셜 게이트) → 응답 파싱 → pred flags, 적대적 리뷰 3R 수렴(`→ T-031·T-032·T-033`; 3R=극단 카디널리티 0건·1건·절단 + 프롬프트 예시 비파싱화 뿌리 수정 + stop_reason/timeout/게이트 단일화), TDD 168 (PR #11)
3e. ✅ **골든 회의 2건째 — 하드케이스(ⓒ)** — 그린마트 이탈 대응(26전사·6flag): 이중 중첩·반복발화 분해·모순↔번복 근접·교차화자 near-miss·같은 type 복수. judge panel 선정 + 적대적 리뷰 1R(생존 10, `→ T-034` 예측 time 관용 파싱 포함), TDD 191 (PR #12)
3f. ✅ **골든 회의 3건째 — 경계 span·tier2 하드케이스** — 결제 장애 회고(27전사·5flag): 인접 동일화자 세그먼트로 경계 span·tier2를 **채점 경로에서** 스트레스(채점에서 span 타는 첫 골든), 5R 보류 2건 gap을 현행 동작 pin. 적대적 리뷰 1R(생존 2: tier2 load-bearing 교정·견고성 비공허화, `→ T-036` EOL), TDD 216 (PR #13)
4. ✅ **실제 Claude API 실호출 실측** — 골든 3건 종합(Opus 4.8), 정밀도 1.00·재현율 0.87, 실 pred 동결 + 채점 회귀, TDD 229 (PR #14, `measurements/`)
5. ✅ **통계 판정층(ⓑ)** — 공유 패키지 `benchmarks/stats/`(`bench_stats`): 유효표본=회의 원칙, 정확 CI·cluster 부호치환·MDE·수집목표·판정 상태기계·사전등록, 실측 3건=DESCRIPTIVE_ONLY(+3 수집목표), zero-dep·TDD 92 (PR #15)
   - ✅ **max-effort 리뷰 후 정확성 결함 15종 수정**(PR #15 동일 브랜치): 판정 오류 6(반대방향 SIGNIFICANT·플로어 우회·파국 오라벨·은행가반올림·동점 floor·중복 id) + 부트스트랩/검정력 6(NaN 누출·icc² 상관·comparison_power 무성 캡·effect 클리핑·MC p=0·n_eff off-by-one) + 견고성 3(level 가드·prereg 라운드트립·정수 강제). TDD Red 19→Green, **15 스킵틱 적대적 검증 전부 SOUND**, 111테스트(`→ T-037`).
6. 🔜 **파이프라인 통합 착수 (3단계, Python 우선)** — 판단 패널 워크플로우로 5-PR 분해 확정. **PR-1 상태머신 순수 코어**(`pipeline/`, 비동기 형상·ANALYZED 정확히-한번 seam·실패/재시도, 적대적 리뷰 4종 반영, TDD 42) 진행 중. 상세는 위 [3단계](#3-파이프라인-통합--python-우선-mvp-착수).
   - 병행 후보: (a) **실측 회의 확장**(재현율 약점·통계 게이트 해제까지 홀드아웃 +3~ — 파이프라인 완성 후 인제스트로 수집) · (c) 5R 보류 2건 경계 매칭 재설계(실측 데이터 확보됨)

---

## 🔄 갱신 정책

- **작업 시작 전**: 그 작업 항목을 이 문서에서 찾아 🔜로, 하위 체크박스를 구체화한다.
- **작업 완료 후**: 여기 체크박스를 ✅로 바꾸고, 같은 회차에 [`changeLog.md`](changeLog.md)에 한 줄 기록한다.
- **범위를 미룰 때**: 삭제하지 말고 ⏸(v2)로 남겨 "왜 지금 안 하는가"를 보존한다.

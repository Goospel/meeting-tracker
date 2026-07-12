# plan.md — 작업 계획 내비게이터

> **이 문서의 역할**: 앞으로 할 일을 *미리* 적어 두는 살아있는 계획서.
> "지금 어디까지 왔고, 다음에 뭘 하고, 무엇을 일부러 미뤘는가"를 한눈에.
>
> - 큰 그림(왜 이 순서인가)은 [`README.md` 구축 순서](README.md#구축-순서-mvp-우선)가 단일 출처. 여기서는 그걸 **체크박스 단위로 쪼갠 실행 계획**을 관리한다.
> - 완료한 일의 기록은 [`changeLog.md`](changeLog.md), 작업 중 만난 함정은 [`troubleshooting.md`](troubleshooting.md).
> - 상세 스펙은 [`docs/spec.md`](docs/spec.md), 데이터 계약은 [`docs/data-schema.json`](docs/data-schema.json).

**범례**: ✅ 완료 · 🔜 다음 차례 · ⬜ 예정 · ⏸ 의도적 보류(v2) · ⚠️ 리스크/전제

---

## 🎯 지금 상태 (2026-07-12)

**구축 순서 1단계(STT 골든셋 벤치마크)의 "측정 코어"까지 완료·머지.**
**테스트 데이터 확보 방법 확정(2026-07-12)**: 직접 녹음 없이 **합성 골든셋(즉시) + AI-Hub 464(병렬 신청)** 2트랙 — 아래 1단계 참조.
Track A **단일 소스 빌더 + 렌더 레이어 + 마크업 확장 완료**(스크립트→골든→매니페스트→WAV 타임라인까지 크레덴셜 없이 관통, 적대적 리뷰 2회 반영, 161테스트). Track A에서 남은 건 **실제 비-네이버 TTS 렌더뿐(크레덴셜 대기)** — 그 외 파이프라인은 톤 렌더러로 실검증 완료.

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
- ⏸ **통계 판정층** (v2) — McNemar, clustered bootstrap BCa CI, 사전등록 MDE
- ⏸ **STT 어댑터** (v2) — `SttPort` 뒤 Clova/AWS Transcribe Live·Replay 러너 → **크레덴셜 필요**
- ⏸ **화자 귀속 지표** (v2) — critical_speaker_error, DER → 모순·번복은 "같은 사람"이 정의라 필수
- ⏸ **역할 스왑 / contradiction_key** (v2)
- ⏸ **프록시 실증** (v2) — STT 오류를 주입해 Claude 감지 영향 측정
- ⏸ **S7(hedge/flags 채점), S3(마지막 주 의미)** — 리뷰에서 v2로 분류
- ⚠️ **실측 전제**: STT(클로바/AWS) + TTS(Azure/Google) 크레덴셜. **오디오는 녹음 불필요**(위 2트랙) — 남은 전제는 크레덴셜뿐.

### 2. 분석 품질 검증 ✅ 채점 코어(`benchmarks/detection/`, PR #9) · ⬜ 실측(크레덴셜)

> 완벽한 전사본을 입력으로 줬을 때 Claude 모순감지가 얼마나 맞히는가 — per-type precision/recall.
> stage-1 철학 계승: 순수·결정적·**런타임 의존성 0** 채점기 + TDD, mock으로 크레덴셜 없이 선행.

- ✅ **골든 라벨 스키마 + 로더/검증 게이트** (`labels.py`, PR #9) — flag 4종(모순/번복/미해결/재논의) + statements(quote·speaker) + 전사 세그먼트 양방향 역참조 일관성 게이트(무드리프트 방지). 골든 = `docs/data-schema.json`의 완성 회의 1건 재사용(전사 25세그·flag 4).
- ✅ **quote grounding 검증기** (`grounding.py`) — 예측 인용이 전사에 실재하는지 대조(NFC 부분일치 + 토큰 Jaccard≥0.6 폴백). **이중 역할**: 할루시 인용 드롭 + 예측 flag가 건드리는 세그먼트 해소(매칭 키).
- ✅ **감지 채점 하네스** (`score.py`) — 객체탐지식 **그리디 매칭**(같은 type + 세그먼트집합 Jaccard≥0.5로 1:1). 매칭=TP / 미매칭 골든=**놓친(FN)** / 미매칭 예측=**가짜(FP, ungrounded/unmatched 분리)**. per-type P/R/F1 + **localization(type-무관) 이중 채점**으로 라벨만 틀린 경우(type_confusion) 분리 노출.
- ✅ **리포트 + CLI** (`report.py`) — 회의 단위 마크다운(가짜/놓친/타입혼동). mock 예측(faithful→완벽·contaminated→4실패모드) 픽스처로 크레덴셜 없이 end-to-end.
- ✅ **적대적 리뷰 반영**(38에이전트, 25 confirmed): localization을 strict 확장으로 재구성(altitude), grounding 최밀착 선택, 역방향 게이트, 골든 grounding 가드 등 8종. TDD **38테스트**.
- ⏸ quote grounding을 **실제 Claude 출력**에 적용(크레덴셜 대기) · 골든 회의 2건째(합성 또는 공개 라이선스) · 통계 판정층(stage-1과 공유).
- ⚠️ 전제: 실측은 Claude API 크레덴셜 — **측정 하네스·채점기 골격은 mock으로 선행**(이 단계).

### 3. 파이프라인 통합 ⬜

- ⬜ 단일 사용자·단일 ECS 업로드→STT→분석 상태머신 (`UPLOADED→…→DONE`)
- ⬜ Spring 오케스트레이터 + DB 상태 단일 진실원
- ⬜ 웹훅 보안 (HMAC 서명 + nonce 리플레이 방지)

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
3. ✅ **2단계 분석 채점 하네스 골격** — 골든 라벨·grounding·그리디 매칭·리포트, mock 예측으로 크레덴셜 없이 관통, TDD 29 (PR #9, `benchmarks/detection/`)
4. 🔜 **다음** — ⓐ 실제 Claude 감지 적용(크레덴셜 대기) 또는 ⓑ 1단계 통계 판정층 설계 문서화(크레덴셜 없이 방법론 확정 가능) 또는 ⓒ 골든 회의 2건째(하드케이스)

---

## 🔄 갱신 정책

- **작업 시작 전**: 그 작업 항목을 이 문서에서 찾아 🔜로, 하위 체크박스를 구체화한다.
- **작업 완료 후**: 여기 체크박스를 ✅로 바꾸고, 같은 회차에 [`changeLog.md`](changeLog.md)에 한 줄 기록한다.
- **범위를 미룰 때**: 삭제하지 말고 ⏸(v2)로 남겨 "왜 지금 안 하는가"를 보존한다.

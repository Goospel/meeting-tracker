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
다음 자연스러운 갈래는 (a) 1단계 나머지 = 실제 STT 어댑터로 실측, 또는 (b) 2단계 = 분석 품질 검증.
둘 다 **외부 전제**(실제 한국어 회의 오디오 + 클로바/AWS 크레덴셜)가 걸려 있어, 그 전에 할 수 있는 코어 작업을 먼저 소진한다.

---

## 구축 순서 — 6단계 (README 기준)

### 1. STT 골든셋 벤치마크 *(제품 성패 지점)*

> 한국어 회의의 숫자·고유명사 STT 정확도를 실측해, 모순감지의 입력 신뢰도를 먼저 검증한다.

- ✅ **측정 코어** — `benchmarks/stt/`, 런타임 의존성 0, TDD 110 테스트 (PR #4)
  - ✅ CTER(치명 토큰 값 등가 채점) — sub=가짜모순 / del=놓친모순 / ambiguous=needs_review 분리
  - ✅ 한국어 수·날짜·시각 파서 (Sino/고유어/소수/범위/단위)
  - ✅ 적대적 코드리뷰 2라운드 (F1~F13, R1~R15 회귀 고정)
- ⏸ **통계 판정층** (v2) — McNemar, clustered bootstrap BCa CI, 사전등록 MDE
- ⏸ **STT 어댑터** (v2) — `SttPort` 뒤 Clova/AWS Transcribe Live·Replay 러너 → **크레덴셜 필요**
- ⏸ **화자 귀속 지표** (v2) — critical_speaker_error, DER → 모순·번복은 "같은 사람"이 정의라 필수
- ⏸ **역할 스왑 / contradiction_key** (v2)
- ⏸ **프록시 실증** (v2) — STT 오류를 주입해 Claude 감지 영향 측정
- ⏸ **S7(hedge/flags 채점), S3(마지막 주 의미)** — 리뷰에서 v2로 분류
- ⚠️ **실측 전제**: 실제 한국어 회의 오디오 + 클로바/AWS 크레덴셜 확보 시 어댑터부터 착수

### 2. 분석 품질 검증 🔜(코어는 지금 착수 가능)

> 완벽한 전사본을 입력으로 줬을 때 Claude 모순감지가 얼마나 맞히는가 — per-type precision/recall.

- ⬜ 골든 라벨 스키마 정의 (모순·번복·미해결·재논의 4종 + quote span)
- ⬜ 감지 채점 하네스 (precision/recall/F1, per-type)
- ⬜ quote grounding 검증기 — Claude 인용이 전사본에 실재하는지 사후 대조, 근거 잃은 flag 드롭
- ⬜ 골든 회의 전사본 1~2건 (합성 또는 공개 라이선스)
- ⚠️ 전제: Claude API 크레덴셜 (측정 하네스 골격은 mock으로 선행 가능)

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

1. **문서 골격 정비** — 🔜 plan / changeLog / troubleshooting (이 PR)
2. **2단계 분석 채점 하네스 골격** — mock 전사본·mock 감지 결과로 채점기부터 TDD
3. **1단계 통계 판정층 설계 문서화** — 크레덴셜 없이도 방법론은 확정 가능

---

## 🔄 갱신 정책

- **작업 시작 전**: 그 작업 항목을 이 문서에서 찾아 🔜로, 하위 체크박스를 구체화한다.
- **작업 완료 후**: 여기 체크박스를 ✅로 바꾸고, 같은 회차에 [`changeLog.md`](changeLog.md)에 한 줄 기록한다.
- **범위를 미룰 때**: 삭제하지 말고 ⏸(v2)로 남겨 "왜 지금 안 하는가"를 보존한다.

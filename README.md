# WordsFilter — 회의 흐름/모순 추적기

회의/토의 녹음을 **인터랙티브 대시보드**로 바꾸는 웹 서비스.
일반 회의요약 툴과 달리, 핵심은 **"흐름/모순 추적기(Flow & Contradiction Tracker)"** —
회의 중 참석자가 자기/상대 말을 까먹어 흐름이 틀어지는 지점을 잡아낸다.

감지 4종: `모순`(같은 사람 앞뒤 다른 말) · `번복`(확정 결정이 조용히 뒤집힘) ·
`미해결`(꺼내놓고 안 다룬 안건) · `재논의`(이견이 결론 없이 넘어감).

> 상세 스펙: [docs/spec.md](docs/spec.md) · 데이터 계약: [docs/data-schema.json](docs/data-schema.json)

## 상태

**설계 확정 · 구현 전.** 기존 파이썬 프로토타입(Cowork 산출물)은 폐기하고 아래 스택으로 새로 짓는다.

## 기술 스택 (확정)

| 레이어 | 선택 |
|---|---|
| 프론트 | React + Vite + TS + Tailwind + shadcn/ui + Recharts (S3 + CloudFront) |
| 인증 | Amazon Cognito (authN) + Postgres memberships (authZ) |
| 백엔드 | Spring Boot on ECS Fargate + ALB |
| STT | Naver CLOVA Speech (`SttPort` 추상화로 AWS Transcribe 스왑 가능) |
| 분석 | Anthropic Claude (롱컨텍스트 단일 패스, 구조화 JSON + quote grounding) |
| 데이터 | RDS PostgreSQL (RLS 멀티테넌시) + S3 (오디오·전사 원문) |
| IaC / CI | Terraform + GitHub Actions (OIDC) |

STT 근거: 한국어 회의 CER — 클로바 8.4% vs AWS Transcribe 26% (RTZR AI-Hub 벤치마크).

## 구축 순서 (MVP 우선 — 인프라보다 가정 검증 먼저)

1. **STT 골든셋 벤치마크** — 실제 한국어 회의로 클로바 vs Transcribe 숫자·고유명사 정확도 실측 (제품 성패 검증 지점)
2. **분석 품질 검증** — 완벽 전사본에 Claude 모순감지, per-type precision/recall 하네스 구축
3. **파이프라인 통합** — 단일 사용자·단일 ECS로 업로드→STT→분석 상태머신
4. **프론트 UX** — 타임라인 리본·상충 발언 비교·grounding 하이라이트
5. **멀티테넌시 + 인증** — Cognito + RLS + 테넌트별 비용 쿼터
6. **인프라 하드닝** — Terraform, api/worker 분리 + SQS/DLQ, blue/green

## 개발 셋업 (2대 기계: 데스크톱 + 랩탑)

- **코드 동기화**: git (GitHub private). `main` 직접 작업 금지 → 브랜치 → PR → 확인 → 머지.
- **비밀 관리**: git 에 비밀 안 넣음. 단일 소스 = **AWS SSM Parameter Store**, 각 기계는 `aws sso login` 으로 당겨쓰기(기계 간 복사 금지). 필요한 변수 목록은 [.env.example](.env.example).
- **툴체인 고정**: JDK / Node 버전을 `.sdkmanrc` / `.nvmrc` 로 커밋 예정(두 기계 재현성).

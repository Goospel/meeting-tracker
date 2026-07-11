# changeLog.md — 작업 로그

> **이 문서의 역할**: 언제 무슨 작업을 했는지의 기록. **매 작업(=대체로 PR 단위)이 끝날 때마다** 맨 위에 한 항목 추가.
>
> - 앞으로 할 일은 [`plan.md`](plan.md), 작업 중 만난 함정은 [`troubleshooting.md`](troubleshooting.md).
> - 최신이 맨 위(역순). 날짜는 KST 절대일자.
> - 한 항목 = `날짜 · 제목 (PR #번호)` + 무엇을/왜. 코드 세부는 커밋·PR에 있으니 여기선 **의도와 결과**만.

**태그**: `feat` 기능 · `docs` 문서 · `chore` 잡무/설정 · `fix` 수정 · `refactor` 구조개선 · `test` 테스트

---

## 2026-07-12

### feat · Track A 합성 골든셋 빌더 — 스크립트 하나 → 골든 + TTS 매니페스트 (이 PR)
- `stt_bench/synth.py` 신설: 인라인 마크업 회의 스크립트(`[[surface|TYPE|key]]`)에서 **CTER 골든 JSON**(문자 오프셋·canonical을 파서로 자동 산출)과 **TTS 렌더 매니페스트**(마크업 제거)를 **같은 소스에서** 파생. → 골든↔렌더 오디오 **무드리프트**, 수동 오프셋 오류 원천 차단.
- 검증 정직화(리뷰 반영): 오프셋 불변식(`text[cs:ce]==surface`)은 자동 계산이라 구성상 성립하고 `validate_golden`이 **실검사**한다. 반면 canonical은 파서 파생이라 게이트의 canonical 대조는 자명하게 통과할 뿐(파서 오파싱은 **회귀 테스트**가 방어) — "게이트 통과=검증"으로 읽히지 않도록 문서·docstring 수정.
- **max-effort 코드리뷰 반영**(다중에이전트 10앵글→검증→스윕): ① 오탈·불균형 마크업 즉시 에러(무성 실패 차단) ② AMOUNT 통화 `or "KRW"` 날조 제거(파서 그대로) ③ fixture TIME 토큰 `[[오후 세 시]]` 재태깅(meridiem 소실→오전/오후 반전 미채점 수정) ④ 마크업 필드 strip·3파트 초과 에러 ⑤ 문서 정직화 ⑥ CLI `--manifest-out`로 매니페스트도 산출. 회귀 8종 추가. 보류: PROPER_NOUN aliases·flags.manual 마크업 슬롯(커버리지 공백).
- 첫 합성 골든 `fixtures/synth/budget_reversal.script.json` → `fixtures/golden/synth_budget_reversal.json`: 화자 3인·치명토큰 7종·**같은 화자 번복 2건**(예산 3천만→5천만, 출시 8월 셋째주→9월 초) 심음 — 2단계 감지 하네스 재사용 대비.
- `golden.py`에 `golden_from_data()` 분리(파일읽기↔파싱), 커밋 골든이 스크립트에서 **재생성 가능**함을 회귀로 고정.
- **TDD**: `tests/test_synth.py` 11테스트(Red→Green), 전체 **121 통과**(기존 110 무회귀). Windows cp949 콘솔 함정(T-027) CLI에 선제 적용.
- **왜**: 녹음 없이 벤치마크를 돌리는 Track A의 토대. 스크립트=정답이라 CTER 채점기 자체를 크레덴셜 없이 end-to-end 검증. 남은 건 비-네이버 TTS 렌더뿐.

### docs · plan.md에 테스트 데이터 확보 전략(2트랙) 반영 ([PR #6](https://github.com/Goospel/meeting-tracker/pull/6))
- 다중에이전트 조사(5갈래 웹조사 → 종합) 결과를 [`plan.md`](plan.md) 1단계에 박음: **직접 녹음 없이** 벤치마크 데이터 확보 = **Track A 합성 골든셋(즉시)** + **Track B AI-Hub 464(병렬 신청)** + Track C 국회 속기록(후순위).
- **왜**: 벤치마크 병목은 오디오가 아니라 '신뢰 레퍼런스 전사(골든)'다. 합성은 스크립트=정답이라 100% 통제, AI-Hub 464는 사람검수 전사·다자·화자라벨 제공.
- 핵심 제약 명시: 벤치 대상에 클로바 포함 → 합성 렌더는 **반드시 비-네이버 TTS**(벤더 편향 회피). 오디오·전사는 라이선스상 로컬 전용, repo엔 CTER 수치 + 생성 스크립트만 공개.
- 다음 착수 = **Track A 합성 골든셋 1건**(크레덴셜 없이 스크립트→골든→채점기 관통).

### docs · 프로젝트 추적 문서 3종 골격 신설 ([PR #5](https://github.com/Goospel/meeting-tracker/pull/5))
- `plan.md`(작업 계획 내비게이터), `changeLog.md`(이 파일), `troubleshooting.md`(함정 기록+승격) 생성.
- **왜**: 작업이 여러 세션·PR로 길어지며 "다음 뭐 하지 / 이거 전에 겪지 않았나"가 흩어짐. 세 문서로 미래(plan)·과거(changeLog)·함정(troubleshooting)을 분리해 마찰 없이 누적.
- 기존 STT 벤치마크 작업의 실제 이력·함정을 시딩해 빈 골격이 아니라 바로 쓰는 상태로 시작.

## 2026-07-11 ~ 07-12

### feat · STT 벤치마크 측정 코어 — 한국어 치명 토큰 오류지표(CTER) ([PR #4](https://github.com/Goospel/meeting-tracker/pull/4))
- 구축 순서 1단계의 "측정 코어" 구현. `benchmarks/stt/` — **런타임 의존성 0, TDD 110 테스트.**
- **CTER**(Critical Token Error Rate): 전체 CER이 아니라 치명 토큰(금액·날짜·고유명사·단위)을 **값 등가로** 채점(3천만=삼천만=30,000,000). sub(값 치환)=가짜 모순 후보 / del(삭제)=놓친 모순 후보 / ambiguous=needs_review를 **분리 집계**.
- 한국어 수·날짜·시각 파서: Sino(만/억/조)·고유어 수관형사·소수·범위(이삼천만)·단위 카테고리.
- **적대적 코드리뷰 2라운드**로 파서·채점기 무성 실패 교정 — F1~F13(13종) + xhigh R1~R15(15종)을 회귀 테스트로 고정. 개별 패치가 아니라 **메커니즘 수준 일반화**(수/단위 문법경계 · 숫자+마커 단일 탐색기 · 스팬 투영 경계삽입 · 게이트 완전동치).
- **왜**: 제품 신뢰가 "한국어 숫자 STT"라는 가장 약한 고리 위에 얹혀 있어, 인프라 짓기 전에 이 가정부터 측정 가능한 형태로 깨봄.

## 2026-07-11

### docs · '연결된 그래프' 시각화 컨셉 섹션 추가 ([PR #3](https://github.com/Goospel/meeting-tracker/pull/3))
- 회의를 노드-링크 그래프로 보는 관점(모순·번복=엣지, 고립 노드=미해결)을 README에 정리. React Flow / force-directed 라이브러리 방향 명시.

### docs · 포트폴리오용 README 보강 ([PR #2](https://github.com/Goospel/meeting-tracker/pull/2))
- 감지 4종·아키텍처(mermaid)·기술 스택·핵심 결정(Why)·리스크 대응·구축 순서를 포트폴리오 수준으로 정리.

### chore · .gitattributes 추가 — CRLF 줄바꿈 정규화 ([PR #1](https://github.com/Goospel/meeting-tracker/pull/1))
- 2대 기계(데스크톱/랩탑) 간 줄바꿈 phantom diff 방지.

### chore · 프로젝트 부트스트랩 — git 초기화 및 설계 문서 정리
- 기존 Cowork 파이썬 프로토타입 폐기, meeting-tracker로 새 스택 재시작. `docs/spec.md`·`docs/data-schema.json`·`.env.example`·`.gitignore` 초기 정리. (부트스트랩 커밋만 예외적으로 main 직접.)

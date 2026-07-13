# changeLog.md — 작업 로그

> **이 문서의 역할**: 언제 무슨 작업을 했는지의 기록. **매 작업(=대체로 PR 단위)이 끝날 때마다** 맨 위에 한 항목 추가.
>
> - 앞으로 할 일은 [`plan.md`](plan.md), 작업 중 만난 함정은 [`troubleshooting.md`](troubleshooting.md).
> - 최신이 맨 위(역순). 날짜는 KST 절대일자.
> - 한 항목 = `날짜 · 제목 (PR #번호)` + 무엇을/왜. 코드 세부는 커밋·PR에 있으니 여기선 **의도와 결과**만.

**태그**: `feat` 기능 · `docs` 문서 · `chore` 잡무/설정 · `fix` 수정 · `refactor` 구조개선 · `test` 테스트

---

## 2026-07-13

### fix · 통계 판정층 max-effort 리뷰 — 판정 정확성 결함 15종 수정 ([PR #15](https://github.com/Goospel/meeting-tracker/pull/15), 동일 브랜치)
- 커밋 `3aedc0f` 직후 `/code-review ultra`(클라우드 미가동 → 로컬 max-effort 폴백: 10앵글 파인더 → 1표 검증 → 스윕)로 신설 `bench_stats`를 감사. 판정층의 **존재 이유(극소 n 정직성)를 정면으로 깨는 오판정** 다수 확정 → TDD(Red 19 → Green)로 수정 후 **15 스킵틱 적대적 검증 전부 SOUND**(무회귀). 전체 **111테스트**(92+19).
  - **판정 오류(가장 위험)**: ① `verdict_paired`가 recall(성공/n)이 아닌 원 카운트 차를 비교 → 분모 다르면 **열등한 감지기를 SIGNIFICANT로 반대방향 오판정**(B가 전 회의 우수한데 A 유의) → n_a≠n_b fail-loud. ② 빈 회의(n=0)가 `n_clusters`를 부풀려 **추론 플로어 우회**(3실회의+3빈=INCONCLUSIVE) → 모든 추론을 유효회의(n>0)수 기준으로. ③ 분산 0 파국이 FAILS보다 먼저 DEGENERATE로 삼켜짐(0% recall이 "무변동") → 파국 검사를 앞으로. ④ `k=round(point·n)` 은행가반올림이 .5 tie에서 거짓 MEETS/FAILS → round-half-down(하한)/up(상한). ⑤ 동점 회의(d=0)가 부호검정 floor의 n 부풀림 → n_informative 기준. ⑥ 중복 cluster_id 무성 드롭 → fail-loud.
  - **부트스트랩·검정력**: ⑦ `cluster_bootstrap_ci`가 n=0 회의 미필터 → 리샘플 NaN이 CI 경계로 누출(pooled와 불일치) → 필터+fail-loud. ⑧ icc 생성모형이 상관을 **icc²로 실현**(한 자릿수 과소, 검정력 과대낙관) → λ=√icc. ⑨ `comparison_power` 도달 불가 시 **시뮬 안 한 n=41 조용히 반환** → fail-loud(+`max_clusters`). ⑩ baseline+effect>1 무성 클리핑(명목≠실제) → 검증 raise. ⑪ MC 치환 p=ge/n_mc에 +1 보정 없어 **무효 p=0** → (ge+1)/(n_mc+1). ⑫ `int(n//deff)` float off-by-one → eps-스냅 floor.
  - **견고성**: ⑬ `cluster_bootstrap_ci` level 미검증(0/1에서 퇴화·크래시) → 가드. ⑭ prereg tuple 필드 dump/load 라운드트립 타입 불일치 → canonical(JSON) 단일출처 정규화. ⑮ `ClusterBinary` 소수 카운트 허용 → 정수 강제.
  - **함정 `→ T-037`**: ④ 첫 수정(양방향 floor/ceil)이 상한을 무조건 넓혀 **파국 민감도를 죽여** 기존 테스트 회귀 — 단일 정수 k로 두 경계를 동시에 보수화 불가, tie만 가르는 방향 반올림으로 재수정. 리포 전체 507(detection 229·stt 167·stats 111).

### feat · 통계 판정층(ⓑ) — 극소표본에서 정직한 CI·검정·MDE (신설 공유 패키지 `bench_stats`) ([PR #15](https://github.com/Goospel/meeting-tracker/pull/15))
- 감지·STT 벤치가 공유하는 **통계 판정층**을 신설(`benchmarks/stats/`, `bench_stats`). 로드맵의 세 요소(McNemar·clustered bootstrap·사전등록 MDE)를 극소표본(회의 3·flag 15) 현실에 **정직하게** 안착. **판단 패널 워크플로우**(4 학파 독립 설계 → 소표본 타당성 적대 검증 → 종합)로 방법론 확정 후 TDD 구현.
  - **핵심 통찰 — 유효표본은 flag 15가 아니라 회의(cluster) 3**: flag는 회의 안에 군집돼 독립이 아니므로 모든 추론은 회의 수준에서만 정당. 이 층의 임무는 "얼마나 좋은가" 판정이 아니라 (a) 극소 n에서 방어 불가능한 주장을 fail-loud 거부 + (b) "얼마나 더 모아야 말할 수 있는가"를 데이터 독립 공식으로 답.
  - **킬러 정직성 함수**: `min_attainable_two_sided_p(n)=2/2ⁿ`·`comparison_floor_n(0.05)=6`(폐형식·관측 무관). n=3이면 **어떤 데이터가 나와도** 쌍체 유의 최소 p=0.25>0.05라 구조적으로 유의 불가.
  - **모듈**: 정확 Clopper-Pearson·정규화 불완전베타·zero-event 상한(FP=0을 정밀도 1.00으로 못박지 않음)·cluster bootstrap(n≤6 전열거 결정적, BCa는 ≥10 cluster 게이트/강등)·cluster 부호치환(판정 1차 검정)·정확이항 MDE·수집목표(중단규칙)·Holm·**9-상태 판정 상태기계**·사전등록 해시 동결.
  - **실측 3건 판정 = DESCRIPTIVE_ONLY**: 재현율 점추정 0.878이나 목표 판정 봉쇄(n<floor 6); pooled CP [0.595,0.983]은 "정직한 CI 아님 — 폭의 낙관적 하한"; 정밀도 FP 0/13은 "1.00" 미주장(zero-event 보수 상한 precision≥0.368); 쌍체 전부 inert(감지기 1개); 수집목표 **+3회의**. `test_measured_verdict.py`로 이 정직한 판정을 회귀 고정.
  - **정직성 설계 결정**(비평이 잡은 결함 2건 수정): ① pooled CP를 clustering 무시로 "폭의 낙관적 하한"으로만 라벨(회의 3점은 회의간 분산 식별 불가 df=2) ② estimand(회의가중 vs flag가중, 회의 크기 4,6,5 불균등이라 값 다름)를 **사전등록 필수 필드**로 승격. **결정성(seed)≠통계적 정밀**을 `n_distinct_resamples`·`granular` 경고로 강제 노출.
  - **1차 종점 = 재현율(회의 가중평균)**, target 0.85, α 0.05로 사전등록(`PREREGISTRATION.md`). 현재 3건은 **탐색적/서술적**으로 못박고 엄격 사전등록은 홀드아웃에만(사후등록 방지). zero-dep(stdlib만)·TDD **92테스트**(known-answer·퇴화 엣지·상태기계·실측 판정 동결). 리포 전체 488(detection 229·stt 167·stats 92).

### feat · 실 Claude API 실호출 실측 — 골든 3건 종합(Opus 4.8) ([PR #14](https://github.com/Goospel/meeting-tracker/pull/14))
- 어댑터(PR #11)가 완성된 뒤 크레덴셜만 대기하던 **실호출 실측**을 처음 수행. 골든 3건(luma 기준선·greenmart 이탈대응·payments 경계) 전사를 `--detector claude`(Opus 4.8)로 실감지 → 채점. 목업(faithful=만점 설계) 대신 실제 모델이 몇 건을 어떻게 맞히는지 측정.
  - **종합 정밀도 1.00 · 재현율 0.87**(15 flag 중 TP 13 / FP 0 / FN 2). 세 회의 모두 **가짜 감지·할루시 인용·타입 혼동 0** — Claude가 "흐름단절"이라 찍은 13건은 전부 진짜.
  - **놓친 2건은 전부 재현율이며 경계가 겹치는 애매한 케이스**: greenmart f3(p4의 무상연장 반대→찬성 **자기모순**이 팀 결정 **번복**에 흡수돼 개별 모순 미표기)·payments f3(미래 약속형 RCA 미해결을 '후속 있음=해결됨'으로 읽음). 즉 실 감지기 약점은 **과잉감지가 아니라 겹치는 경계의 과소감지** — 오탐으로 사용자를 괴롭히기보다 미묘한 중첩 단절을 놓치는, 제품상 유리한 실패 방향.
- **동결 스냅샷 + 채점 회귀 고정**: LLM 출력은 비결정적이라 실호출을 그대로 재현 못 함 → pred 3건을 `measurements/`에 LF로 동결(`→ T-036` EOL 재확인 — CLI `write_text`도 CRLF라 바이트 정규화). 채점기는 순수·결정적이라 동결 pred를 재채점하면 항상 같은 표 → `test_measured_real.py`(13테스트)가 grounding/score 회귀를 **실 API 재호출 없이** 잡는다.
- **데이터 정직성 분리**: `fixtures/`(합성 mock — 채점기 검증용)와 `measurements/`(실 API 산출)를 디렉터리·README로 명확히 구분(어떤 성능도 mock으로 주장하지 않음 vs 실측은 출처·모델·일자 명기). 감지 229테스트(216 → +13).

### feat · 골든 회의 3건째 — 경계 span·tier2 하드케이스(결제 장애 회고) ([PR #13](https://github.com/Goospel/meeting-tracker/pull/13))
- 골든 1·2는 모든 인용이 tier1 부분일치+time 분해 한 경로에 몰려 **경계 span grounding·tier2 퍼지가 채점 파이프라인에서 발화한 적이 없었다**(단위 테스트만 커버). 이 세 번째 골든 `payments_postmortem.json`(27전사·5flag)은 **인접 동일화자 세그먼트(STT가 한 발화를 쪼갠 것을 모사)**를 넣어 그 두 경로를 **채점 경로에서** 스트레스한다.
  - **경계 span(채점에서 span 타는 첫 골든)**: f1의 첫 진술이 s6·s7(같은 화자 p2 연속)에 쪼개짐 → 예측은 경계 인용 하나로 내 `ground_quote_span`이 `{s6,s7}` 회수, 골든은 span=False라 세그먼트별로 쪼개 라벨 → 같은 segset `{s6,s7,s14}`로 수렴해 매칭.
  - **제 몫 하는 픽스처**: `faithful`(경계 span 정탐 포함 만점)·`contaminated`((2,2,3)·type_confusion 1(f2↔cp2)·tainted 1(f1↔cp1)·tier2 재정렬 정타 cp3) + 리플레이 응답으로 어댑터 실경로 관통. `validate_golden` 결정적 게이트 통과.
  - **plan line 67의 5R 보류 2건을 명시 pin(재설계 아님)**: gap ① 모호성 정책 비대칭(단일=첫출현 추측 vs 스팬=거부), gap ② 경계 퍼지 tier 부재(verbatim 경계 `{s17,s18}` vs 1단어 의역 `∅` 소실 — 단일 세그먼트 의역은 tier2로 구제되는데도). 매칭 의미론 변경은 실측 후로 미루고 현행 동작만 고정.
- **적대적 리뷰 1R(6앵글 파인더 → 후보별 스크립트 검증; 후보 5, REFUTED 3, 생존 2) 반영**:
  - **[1 CONFIRMED] tier2가 채점 경로에서 load-bearing이 아니었다**: f4(재논의)가 2세그뿐이라 tier2가 죽어도 tier1 하나로 J=0.5 문턱을 충족해 매칭이 서서 **tier2 회수가 채점에 잉여**였다(골든 표방의 절반 미실현). → f4를 3세그(`{s20,s21,s22}`)로 확장해 tier2를 판별적으로 교정(죽이면 J=1/3<0.5로 f4 매칭 소실, (1,3,4)로 붕괴). `test_tier2_is_load_bearing_in_scoring`로 고정.
  - **[2 PLAUSIBLE] 견고성 가드 공허**: 모든 gold↔pred Jaccard가 {0,1}뿐이라 임계 0.5·동점 내용-타이브레이크가 미발화. → J=0.5 경계·동점 경합 예측을 현행 동작으로 pin하는 테스트 추가(비공허화).
  - **문면 정직성 3종(REFUTED지만 골든 정합 위해 정리)**: `speaker_stats.turns`를 전사에서 병합턴으로 파생·`action_items` owner를 f3(RCA 미해결)와 정합(미지정)·seq 번호 갱신.
- **함정 `→ T-036`**: 빌더 `write_text`가 Windows에서 `\n`→CRLF로 조용히 변환 → 기존 LF 골든과 불일치. `newline=""`로 교체 + 기존 CRLF LF 정규화(글로벌 「EOL 보존」 원칙 구체 재발).
- **TDD Red→Green, 감지 216테스트**(195 → +21). 순수·결정적·런타임 의존성 0·크레덴셜 0 유지. 데이터는 전부 합성(정직성).

### feat · 골든 회의 2건째 — 하드케이스(그린마트 이탈 대응) + 예측 time 관용 파싱 (ⓒ) ([PR #12](https://github.com/Goospel/meeting-tracker/pull/12))
- 첫 골든(luma)은 4유형을 한 건씩 담은 기준선. 이 두 번째 골든 `greenmart_meeting.json`(26전사·6flag)은 **순진한 감지기·채점기를 스트레스**하도록 설계한 하드케이스다(구축 순서 2단계 ⓒ). 다양성 확보를 위해 **5개 시나리오 seed 병렬 설계 → 3 심사자 judge panel**로 우승작 선정(만장일치), 정본으로 채택.
  - **하드케이스 5종**: ① 이중 중첩(한 라인이 두 flag 근거 — s16=자기모순 앵커+미해결, s21=번복+모순이 '무상 연장' 어휘 공유) ② 반복발화 분해(f5 근거 인용이 디코이 s5·근거 s18에 byte-동일 → 오직 time으로만 갈림) ③ 모순↔번복 근접(같은 라인·어휘에 두 라벨 공존 → type_confusion 유발) ④ 교차화자 near-miss(다른 화자 대립은 모순이 아니라 재논의) ⑤ 같은 type 복수(모순 2·미해결 2).
  - **제 몫 하는 픽스처**: `faithful`(만점 관통)·`contaminated`(실패모드 주입 — 모순↔번복 뒤바꿈·교차화자 오라벨로 **type_confusion 2건**, 중첩 미해결 놓침, 할루시/tainted) + 리플레이 응답으로 **어댑터 실경로**까지 관통. `validate_golden` 결정적 게이트로 grounding 정/역 일관성 보증.
- **적대적 리뷰 1R(3앵글 파인더 → 후보별 스크립트 검증; 생존 10, REFUTED 1) 반영**:
  - **[측정 코어 보강 `→ T-034`]** 반복발화 판별이 **numeric time 단일 신호**에 의존 → 실측 LLM이 time을 문자열(`"760"`)·시각(`"12:40"`)로 내면 정탐이 디코이로 붙어 **무성 FN+FP**. 예측 경로에 `_coerce_pred_time`(숫자문자열·`MM:SS` 관용 파싱, 골든 strict는 그대로 엄격) 추가 + 파라미터화 회귀.
  - **라벨 정합성 2종**: f3(모순) 앵커 s21의 "다시 생각해보니"가 자기공개적 입장전환이라 '은폐된 자기모순' 정의와 충돌 → 제거해 텍스트-라벨 일치(제목·설명도 '개인 의견 번복' 프레이밍 제거); s14가 f6과 같은 '소프트 제안→후속 없음'이라 **미표기 미해결 FP 함정** → 확정 보상안 수용하는 닫힌 반응으로 바꿔 비대칭 해소.
  - **테스트 엄정성 5종**: time-blind 회귀를 부등식→디코이 하이재킹 정확값(`{s5,s19}`·tp==5)으로, 같은 type 2건의 1:1 그리디 매칭 실스트레스+순서 불변, 리플레이 정밀도(fp==0·type_confusion==[]), `no_evidence` FP 사유 커버, 스코프 한계(span·tier2 미발화 — 후속 골든) 문서화.
- **코드리뷰(xhigh) 후 보강 4종** (머지 전 동일 브랜치):
  - **[크래시 회귀 `→ T-035`]** `_coerce_pred_time`의 `MM:SS` 분기가 `int()`를 `str.isdigit()`로 가드 → 십진 외 유니코드 숫자(위첨자 `²`·아래첨자 `₂`·원문자 `⑨`)에서 `isdigit` True인데 `int()` 거부로 **uncaught ValueError가 채점 배치 전체를 죽임**("예측은 강등, 배치 안 죽게" 불변식 위반, diff 이전엔 안전하던 입력의 회귀). `isdigit()`→`isdecimal()`(Nd=int 도메인 부분집합)로 원천 차단, TDD Red→Green + 파라미터화 회귀.
  - **테스트 엄정성 3종**: 1:1 그리디 매칭 테스트가 골든측만 검증(예측 재사용 회귀 못 잡음) → 예측측 소비(`{pA,pB}` 매칭·`fp==0`) 고정; 순서 불변 테스트의 type_confusion 개수 비교 → `(골든,예측) 쌍` 집합 비교로 강화; 리플레이 픽스처 읽기 `utf-8`→`utf-8-sig`(프로덕션 경로와 정렬, BOM 재저장 대비).
- **TDD Red→Green, 감지 195테스트**(168 → +27). 실측 없이 채점기·어댑터·하드케이스 판별을 크레덴셜 0으로 관통 검증. 데이터는 전부 합성(정직성 유지).

### feat · 감지 어댑터 레이어 — 골든 전사 → Claude 감지 → pred flags (ⓐ, 크레덴셜 게이트·리플레이 관통) ([PR #11](https://github.com/Goospel/meeting-tracker/pull/11))
- 지금까지 채점기(score/report)는 **mock pred JSON**을 먹였다. 실제로는 전사본을 Claude에 넣어 flag JSON을 받아야 한다 — 그 **앞단(어댑터)**을 채운 게 이 작업(구축 순서 2단계 ⓐ). Track A 렌더 레이어와 같은 패턴: **Port + 크레덴셜-불요 실동작 구현 + 크레덴셜-게이트 확장점**. `detect_bench/detect.py` 신설:
  - **프롬프트 빌더**(`build_detection_prompt`) — 전사(발화자 id·시각·텍스트)와 4유형 정의·엄격 JSON 계약만 제시. **정답 누출 0**: meta.summary/decisions·golden flags(제목/설명)·seg.flags 역참조는 절대 안 넣음.
  - **응답 파서**(`parse_detection_response`) — Claude 자유형식 출력(코드펜스·서문/후문 산문)에서 flags JSON을 견고 추출. 순수·결정적 코어.
  - **감지 포트** — `ReplayDetectorPort`(캔드 응답 재생 = **크레덴셜 0으로 프롬프트→파싱→채점 전 파이프라인 실검증**, ToneTtsPort 대응) / `ClaudeDetectorPort`(실제 Anthropic Messages API를 **stdlib urllib**로 — anthropic SDK 안 실어 **런타임 의존성 0 유지**, `ANTHROPIC_API_KEY` 없으면 `DetectorCredentialError`). `get_detector` 팩토리 게이트.
  - **CLI**(`python -m detect_bench.detect`) — 골든 전사 → pred flag JSON 산출 → 기존 `report --pred`가 그대로 소비. 리플레이 픽스처(`fixtures/response/luma_meeting.claude.txt`)로 전사→감지→채점 만점 관통(크레덴셜 없이).
  - labels 리팩터: `load_pred_flags`를 `coerce_pred_container`+`pred_flags_from_items`로 분리(파일 로더와 어댑터가 **같은 강등 규칙** 공유).
- **적대적 코드리뷰 2R 수렴**(측정 코어라 find→verify→sweep):
  - **1R**(3에이전트 3렌즈, 확정 6+): **HIGH** 파서가 컨테이너를 '첫 파싱값'으로 잡아 산문 속 stray 배열(`[]`·`[1,2]`·statements 배열)이 진짜 flags를 **무성 강탈**(빈 `[]`→"0건 감지" 둔갑=벤치 오염) → 의미 기반 선택으로 재작성(`→ T-031`). 그 외: `_text_from_api_response` 비문자열 text 블록 TypeError, `_urllib_post` HTTP 4xx/5xx 에러본문 소실, 비-dict `meta` 골든 AttributeError, **프롬프트 예시가 골든 f1 좌표(p2@1240·2510) 노출→낙관 편향**, 데드 임포트.
  - **2R 검증 스윕**(1에이전트, HIGH 1+MED 1): 1R 수정이 **덜 막은** 2건 — ⓐ 빈/예시 **`{"flags":...}` 래퍼 에코**가 위치로 강탈(첫 컨테이너 방식의 잔존 구멍) → **실제 flag 수 최대(동수면 마지막)** 선택으로, ⓑ bare 배열 `all()` 게이트가 원소 하나 비정상에 배열 전체 버려 **부분손실** → `any()` 게이트로 전체를 강등 경로로.
- **적대적 코드리뷰 3R(xhigh: 10앵글 파인더 → 후보별 검증 1표 → 갭 스윕; 확정 14+개연 1, REFUTED 1) 반영** — 2R 휴리스틱이 **극단 카디널리티(0건·1건·절단)** 에서 뒤집히는 것을 실측으로 잡고 뿌리부터 수정(`→ T-032`):
  - **뿌리(프롬프트)**: 예시를 **JSON 문법 밖 표기**(`<...>` 플레이스홀더)로, 0건 규칙에서 파싱 가능한 `{"flags": []}` 리터럴 제거 → verbatim 에코가 파서 후보 자격 자체를 상실. 불변식 테스트: `parse(프롬프트 원문)` = 반드시 ValueError(수정 전엔 프롬프트에서 더미 예시가 추출됐다).
  - **파서**: 카운트를 flag스러운 원소로 한정(statement-dict 기형 에코 강탈 차단) · 빈/무내용 컨테이너는 내용 있는 차선에 양보(bare 배열 답 + 후행 `{"flags": []}` 에코 0건 둔갑 차단) · fallback '첫 후보' → **내용 최다**(서두 에코의 bare 배열 강탈 차단) · `"flags"` 텍스트 존재+유효 컨테이너 0 = **절단 의심 클린 에러**(완성 조각 부분 인양 금지 — 10건→1건 무성 축소 차단) · `RecursionError` 격리(퇴화 중첩 `[`*N 트레이스백 차단) · 전량 비-dict 가드를 CLI 조기검증에서 파서 계약으로 이동.
  - **실 API 포트**: `stop_reason=max_tokens` **클린 에러**(절단 텍스트가 파서로 새는 진입점 차단) + `--max-tokens` 노브 배선(팩토리·CLI — 절단 시 사용자 완화 수단) · `urlopen` **timeout 300s**(스톨=행 → 클린 에러) · 크레덴셜 게이트 **생성자 단일 지점**화(팩토리/직접 생성 메시지 드리프트 제거, 공백 키 strip 거부) · 비문자열 `error.message` TypeError 차단.
  - **CLI/labels**: 출력 쓰기를 try 안으로(쓰기 OSError만 트레이스백으로 새던 비대칭 제거) · NaN/±Infinity → null 강등 + `allow_nan=False`(pred 파일 RFC 8259 보증) · `pred_flags_from_items` 컨테이너 가드(래핑 dict 오진 차단) · `_is_num` isfinite(±Infinity가 `[infs]`로 프롬프트에 새던 것 차단) · `coerce_pred_container` 허위 공유 주장 독스트링 수정(bare `[]`는 로더=0건 수용/어댑터=fail-loud로 **의도적 비대칭** 문서화) · 참석자 렌더 기형(`(, 영업)`)·null title `None` 누출 수정 · T-027 reconfigure 5벌째 복붙을 `cliutil.force_utf8_stdio()` 공용 헬퍼로(detect+report).
  - **테스트 위생(갭 스윕)**: `test_cli_claude_without_key_clean_error`에 `delenv` — 키가 설정된 환경에서 단위테스트가 실 API를 때리던 것 차단(`→ T-033`) · 좌표 누출 가드를 1240까지 확장 · 죽은 `FlagType` 임포트 제거.
- **TDD** Red→Green, **168테스트**(107 → 2R 146 → 3R +22, labels 회귀 3 포함). e2e: 리플레이 픽스처 전사→pred→report 만점, 파서 적대입력(빈/숫자/dict 배열 선행, 래퍼 에코 전후·0건·1건 동수, 단일 flag, 부분강등, 절단, 퇴화 중첩, 추출실패) 전량 무성오답 0. **왜**: 실측(크레덴셜) 전에 **어댑터·프롬프트·파싱**을 크레덴셜 없이 확정 — 크레덴셜 오면 포트만 스왑하면 실측 시작.
- **스코프 밖(다음)**: 실제 API 실호출 실측(크레덴셜) · 골든 회의 2건째 · 통계 판정층.

### feat · 적대적 코드리뷰 5R(xhigh) 반영 — 골든 단일 grounding 복원·힌트 산술 가드·클린 에러 대칭 ([PR #10](https://github.com/Goospel/meeting-tracker/pull/10))
- 보강 3종(아래 항목)에 대한 5번째 적대적 리뷰(xhigh: 10앵글 파인더 → 후보별 검증자 1표 → 갭 스윕, 16확정+스윕 2, REFUTED 1)를 반영. 핵심 축 2개:
  - **골든 경로의 span 확장 부수효과(뿌리 수정)** — 보강 ①의 span 확장이 골든 grounding까지 흘러들어, ⓐ 골든 tier-2 퍼지 인용의 segset이 1→3세그로 부풀어 핵심 세그먼트만 정확히 인용한 **정탐이 Jaccard 1/3<0.5로 FP+FN 동시 처리**(조용한 채점 왜곡), ⓑ main에서 유효하던 골든이 validate_golden 정방향 게이트(span 전 세그먼트 역참조 요구)에서 **오거부**. → 골든 grounding을 **단일 세그먼트(main 의미론 + 힌트)로 복원**(`resolve_flag_segments(span=False)`), span 확장은 신뢰 불가 예측 전용 구제책으로 한정. 경계 걸친 골든 인용은 statement를 쪼개 라벨(기존 규약 그대로).
  - **힌트 산술 가드 잔여 구멍(T-029 계열 완결)** — ⓐ `_num`이 **NaN**(json.loads 기본 허용)을 통과시켜 ==비반사성으로 최근접 필터가 전멸, 유일 창 grounding까지 거부(할루시 FP) → `x == x` 유한성 가드(`T-030`). ⓑ `_pick`이 비숫자/None `start_sec`을 거리 0.0(**최우선**)으로 취급 — span 경로(inf)와 정반대라 숫자 힌트가 정확히 가리키는 세그먼트가 짐 → inf로 정합. ⓒ 창 time 거리를 첫 세그먼트가 아니라 **창 내 최근접 세그먼트**로(힌트가 창 뒤쪽 세그먼트를 가리키는 정당한 케이스 오귀속 방지).
- 그 외 확정 수정: tier-2 **동점** 시 창 밖 첫 출현이 verbatim 창을 가로채던 것(`any(cand in span)` 검사로), 공백-only 세그먼트의 창 concat **이중 공백**(빈 원소 제외), **빈 화자 라벨**(''=='')이 교차화자 스티칭 거부를 무력화하던 것(동질성 판정 불가 → 보수적 창 금지), 예측 `quote:null`이 '할루시 인용'으로 오분류되던 것(**no_evidence 분리**), 골든 statement 필드 엄격성 누수(quote/speaker 비문자열·time_sec/start_sec 비숫자 무성 강등 → 엄격 raise), **falsy id 0** 오거부/무성 치환(존재 검사 `is None`+예측 str 보존), 예측 **전량 비-dict**가 rc=0 정상 리포트로 둔갑하던 것(클린 에러), 골든 null/비-dict 구조 오류의 TypeError 트레이스백(클린 ValueError→rc=2), `_safe`에 HTML 엔티티(&·<·>) 무력화, speaker만 NFC를 우회하던 비대칭 해소, 미지 type 키 부재의 `str(None)`→"None" 조작(명시 센티널 "(type 누락)"), 메타 문자열 필드(severity 등 5종) 타입 가드, 테스트 임시파일 누수.
- **보류 2건**(설계 재작업 — plan.md ⚠️): 단일/스팬 모호성 정책 비대칭, 경계 인용 퍼지 tier 부재. 매칭 의미론 변경이라 실측 데이터 확보 후 자체 리뷰 라운드로.
- **TDD** Red(25 실패 확인)→Green, **107테스트**(82 → +25). e2e: faithful 픽스처 만점·contaminated 예상 열화 확인. **왜**: 실측 투입 전 마지막 신뢰 게이트 — 채점기가 틀리면 이후 모든 측정이 무의미.

### feat · 감지 하네스 실측 전 보강 3종 — 같은-화자 span·반복발화 힌트·변형 type 강등 ([PR #10](https://github.com/Goospel/meeting-tracker/pull/10))
- 2단계 채점 하네스(`benchmarks/detection/`)를 **실제 Claude 출력**에 넣기 전 필수 보강 3종. mock 픽스처에선 안 밟히지만 실측에서 반드시 터지는 3구멍을 TDD로 닫음:
  - **① 인접 세그먼트 걸친 인용** — 경계를 걸친 정당한 인용이 단일-세그먼트 3방법을 다 실패해 통째로 '할루시' 오분류되던 것 → `ground_quote_span`/`_span_grounding` 신설로 **같은 화자** 연속 세그먼트 창(window) substring 매칭. STT는 한 화자 발화를 쪼개므로 경계 매칭은 같은 화자에 한정(교차화자 스티칭 = 실재 연속발화 아님 → 거부).
  - **② 반복 발화 오귀속** — 같은 텍스트가 여러 세그먼트에 나올 때 grounding이 항상 첫 출현을 골라, 후행 출현을 가리키는 정당한 골든이 게이트에서 거부되던 것 → statement의 speaker/time_sec을 grounding 힌트(`_pick`)로 전달해 올바른 출현 선택.
  - **③ 변형 type 라벨 run 중단** — 예측의 미지 type 라벨(`FlagType(raw)` ValueError)이 채점 run 전체를 죽이던 것 → 예측만 per-flag 강등(원문 str 보존, 채점기가 미매칭 FP로 처리), 골든은 여전히 엄격. NFC 정규화 우선 + 영문 별칭표(예측 전용).
- **적대적 코드리뷰 1R**(19에이전트 5각도, 12확정+2sweep): grounding 초안을 **뿌리에서 재설계** — collapse 정규화 일관화, tier 우선 등. **HIGH**: 예측의 비숫자 `time_sec`("00:11" 등 Claude 타임스탬프 문자열)이 `abs(start_sec - time_sec)`에서 TypeError로 run 전체 중단 → `_num()` 숫자 가드로 강등(보강 ②가 보강 ③의 격리 불변식을 새로 깨던 회귀, → `T-029`). labels: 영문 별칭이 골든 엄격성으로 새던 것 차단·NFD/공백 골든 오거부 수정·누락 type 키 강등. report: 강등 미지 type의 파이프(`|`)가 표 열 깨던 것 무력화.
- **적대적 코드리뷰 2R**(11에이전트, 8확정): 1R 재작성이 **새로 만든** 결함까지 잡아 span을 재재설계 — **뿌리**: '같은 화자' 제약을 **신뢰 불가한 예측 화자**에 하드 게이트로 걸어(예측이 화자를 생략·이름표기하면 정당한 경계 인용이 통째로 할루시로 뒤집힘) → 창 화자 동질성을 **전사 세그먼트끼리**로 판정하고 예측 speaker/time은 후보를 좁히는 **필터**로만, 필터 후에도 창이 여럿이면 **grounding 안 함**(추측으로 틀린 창 귀속 = 조용한 점수 오염 방지). tier2 퍼지 단일은 그 세그먼트를 **포함하는** span일 때만 확장(무관 창 하이재킹 차단). **HIGH**: 예측 `quote:null`이 `normalize(NFC, None)` TypeError로 배치 중단 → quote/speaker 비문자열도 ""로 강등(type/time만 막고 quote는 안 막던 불완전 수정). id 없으면 예측은 `pred{i}` 강등(골든은 raise). `_safe`에 `\r` 추가.
- **적대적 코드리뷰 3R**(7에이전트, 2확정): 2R 재작성의 남은 불완전 2건. **HIGH**: STT가 한 화자를 3+ 세그먼트로 쪼갠 흔한 런에서, 같은 위치의 min창과 그 상위집합(superset)창을 '서로 다른 후보'로 세어 mid-run 경계 인용을 모호로 오판·드롭 → 후보를 **최소 커버 창으로 subset 축약** 후 모호성 판정. **medium**: `quote:null`은 막았으나 `statements` **컨테이너 shape**(null/문자열/비-dict 원소)·비-dict flag·비-list flags는 안 막아 `for s in None` 등 TypeError로 배치 중단 → 예측은 shape별 per-flag 강등(골든은 엄격 raise), 구조적 오류는 클린 return 2.
- **적대적 코드리뷰 4R**(4에이전트, 검증 수렴): correctness 결함 0 — 3R까지의 재수정이 새 회귀를 만들지 않음을 확인. 저severity 1건만: `flags` 키 **부재** dict가 subscript KeyError로 암호적 메시지 → `data.get("flags")`로 디스크립티브 ValueError 통일(가드 완성).
- **TDD** Red→Green, **82테스트**(46 → +36). **왜**: ⓐ(실제 Claude 감지 적용)의 신뢰 전제 — 채점기가 실측 입력에서 조용히 틀리지 않도록 실측 전에 격리·경계·정규화 구멍을 먼저 막음. 적대적 리뷰 4R로 수렴(신뢰 불가 예측의 모든 필드/컨테이너 shape에 per-flag 강등 관철).

## 2026-07-12

### feat · 2단계 감지 품질 채점 하네스 — Claude 흐름/모순 감지 vs 골든 라벨 ([PR #9](https://github.com/Goospel/meeting-tracker/pull/9))
- 새 벤치마크 패키지 `benchmarks/detection/`(stt와 형제, **런타임 의존성 0**): 완벽한 전사본을 줬을 때 Claude가 흐름단절 4종(모순/번복/미해결/재논의)을 얼마나 맞히는지 per-type P/R/F1로 잰다. 1단계 CTER의 "가짜/놓친 분리" 철학을 감지층에 적용.
- `labels.py`: 골든/예측 데이터 모델 + 로더 + **양방향 일관성 게이트**(전사 세그먼트가 flag을 역참조 ↔ flag 인용이 그 세그먼트에 grounding — stage-1 오프셋 불변식에 대응하는 무드리프트 방지). 골든 = `docs/data-schema.json`의 완성 회의 1건 재사용(전사 25세그·flag 4).
- `grounding.py`: quote grounding **이중 역할** — ① 할루시 인용(전사에 없음) 드롭 ② 인용→세그먼트 해소로 매칭 키 제공. NFC 부분일치 + 토큰 Jaccard≥0.6 폴백.
- `score.py`: 객체탐지식 **그리디 매칭**(같은 type + 세그먼트집합 Jaccard≥0.5로 1:1). 매칭=TP / 미매칭 골든=**놓친(FN)** / 미매칭 예측=**가짜(FP)**. FP를 ungrounded(할루시)/unmatched(골든에 없음)로 분리. **type-무관 localization** 이중 채점으로 라벨만 틀린 경우(type_confusion, 모순↔번복 혼동)를 분리 노출.
- `report.py` + CLI: 회의 단위 마크다운(유형별 표 + 가짜/놓친/타입혼동 목록). T-027 stdout/stderr utf-8, malformed 골든 조기 차단(return 2).
- **mock 예측 픽스처**로 크레덴셜 없이 채점기 자체를 end-to-end 검증: `faithful`→완벽(P=R=1), `contaminated`→4실패모드(정타·타입혼동·누락·할루시)를 정확히 분리 집계(종합 P0.40/R0.50, localization R0.75로 "찾았으나 라벨 틀림" 드러남).
- **적대적 코드리뷰 반영**(38에이전트 find→verify→sweep, 25 confirmed): 채점 로직 결함 다수 수정 — ① **localization을 strict의 독립 그리디→strict 확장으로 재구성**(겹치는 세그먼트집합에서 localization TP<strict TP가 되던 논리 모순 + strict 정타 골든이 타입혼동에 이중계상되던 것 원천 차단, altitude 수정) ② `ground_quote` **완전일치 우선→최밀착 substring**(첫 substring 오귀속 제거) ③ 검증 게이트에 **역방향(orphan back-ref)** + segment_id 유일성 ④ score가 grounding 안 되는 골든 flag를 조용히 FN 강등→에러 ⑤ `pred_meta` 인덱스화(중복 예측 id 충돌 제거) ⑥ CLI KeyError 클린 처리 ⑦ 리포트 flag_id 이스케이프 ⑧ 죽은 `Statement.segment_id` 제거. 회귀 9종 추가.
- **적대적 코드리뷰 2차 반영**(10앵글 find→verify→sweep, 15 확정): ① **정타(TP) 속 할루시 인용 분리 노출**(`TaintedMatch` + 리포트 🟠 섹션 — 절반만 지어낸 flag가 완전한 정타로 통과해 할루시 방어 주장이 뚫리던 구멍) ② 절삭 인용(`...` 등 구두점-only)이 아무 세그먼트에나 grounding되던 것 차단 ③ Jaccard **동점 타이브레이크를 인덱스→내용(id) 기준**으로(예측 파일 순서가 F1 0.5↔1.0 가르던 순서 의존 제거) ④ statements 빈 예측을 '할루시'가 아닌 **`no_evidence`**로 분리(제3의 실패모드 오염 방지) ⑤ 타입혼동 FP의 리포트 표기 자기모순 해소('골든에 대응 없음'→'라벨 오분류, 🔵 참조') ⑥ bare `pytest` 수집 전멸 해소(`conftest.py`) ⑦ 동어반복이던 결정성 테스트를 **순서-순열 불변 테스트**로 교체. 회귀 8종 추가. 실측 전 전제 3종(멀티세그먼트 인용·동일발화 반복·변형 type 라벨)은 plan ⏸ 기록.
- **TDD** Red→Green, **46테스트**. **왜**: STT(1단계) 위층인 "감지 품질"을 크레덴셜 없이 측정 가능한 형태로 확정 — 실제 Claude 출력을 넣기 전에 채점기가 가짜/놓친을 옳게 가르는지부터 검증.

### feat · Track A 렌더 레이어 + 마크업 문법 확장 ([PR #8](https://github.com/Goospel/meeting-tracker/pull/8))
- **렌더 레이어** `stt_bench/render.py` 신설: `TtsPort` 프로토콜 + 크레덴셜-불요 `ToneTtsPort`(stdlib `wave`만, 화자별 사인 톤) + `render_clip`(매니페스트→WAV 타임라인 + 실제 렌더 시각 리포트) + `get_port` 팩토리. **런타임 의존성 0 불변식**이라 Azure/Google 뉴럴 TTS SDK는 코어에 싣지 않고 `get_port`가 `TtsCredentialError`로 막는 **확장점**만 둔다(크레덴셜 오면 포트만 스왑). `naver`는 벤더 음향 prior 편향으로 애초에 거부(벤치에 클로바 포함).
- **마크업 문법 확장** `synth.py`: `_parse_fields` 신설로 PROPER_NOUN `aliases=`(축약 허용목록)·`manual`(파서 미파생 canonical opt-out→채점기 ambiguous)·`canonical=`(manual 라벨) 지원. 채점기·검증기(`score.py`/`golden.py`)는 이미 aliases/flags.manual을 소비 → **마크업 방출만** 확장. 하위호환(`surface|TYPE|key`) 유지. 대표 fixture 루미에 `aliases=Lumi,루미에` 실어 실증.
- **적대적 코드리뷰 반영**(36에이전트 find→verify→sweep, 27후보 25 confirmed): 무성 실패·크래시 **11종 수정** — ① amplitude>1 int16 포화 OverflowError ② 빈 `aliases=`/`key=` 무성 무력화 ③ `aliases`+`manual` 죽은 별칭 ④ 무명 key + `key=` 이중 지정 ⑤ `--report-out` 부모 미생성 부분산출 크래시 ⑥ `render_clip` ValueError 미포착 ⑦ `gap_sec` 음수 모호 에러 ⑧ 커스텀 포트 `sample_rate=0` ZeroDivision ⑨ `synth.main` stderr utf-8 누락(T-027) ⑩ 빈 surface 게이트 통과 ⑪ 비-list 매니페스트. 보류 4종은 설계상 정당/사전존재(plan 기록).
- **추가 코드리뷰 반영**(`/code-review ultra` 로컬 max-effort, 10앵글+파인더 3): `_parse_fields`/`render.main`의 **가드 비대칭** 6종 수정 — ① 중복 `aliases=` 무성 last-wins(앞 별칭 소실→채점기 가짜 CTER) ② `render.main` 매니페스트 읽기가 `try` 밖→없는 경로 `FileNotFoundError`/깨진 JSON `JSONDecodeError` 트레이스백(→클린 `return 2`, `OSError`까지 포섭) ③ 선행 빈 필드가 무명 key 하위호환 깨고 오해성 'unknown field'(→ raw 인덱스 대신 '첫 비어있지 않은 필드' 판정) ④ 중복 `canonical=` 무성 last-wins ⑤ 빈 `canonical=` surface 무성 fallback ⑥ README 테스트 수 오기. **뿌리**: `_set_key`만 (빈값+중복) 둘 다 방어했는데 뒤에 붙인 setter(`aliases=`/`canonical=`)에 그 가드 누락 → 무성 데이터 손실(→ `T-028`).
- **TDD**: 회귀 포함 `test_render.py`(20) + `test_synth.py` 확장 → 전체 **167 통과**(무회귀). 렌더 산출물(WAV·매니페스트·렌더 리포트)은 파생물이라 gitignore.
- **왜**: Track A의 "녹음 없이 오디오까지" 파이프라인을 크레덴셜 없이 실검증 완료. 이제 Track A에서 남은 건 실제 비-네이버 뉴럴 렌더뿐(크레덴셜 대기).

### feat · Track A 합성 골든셋 빌더 — 스크립트 하나 → 골든 + TTS 매니페스트 ([PR #7](https://github.com/Goospel/meeting-tracker/pull/7))
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

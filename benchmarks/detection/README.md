# 감지 품질 벤치마크 — Claude 흐름/모순 감지 vs 골든 라벨 (측정 코어)

> meeting-tracker [구축 순서](../../README.md#구축-순서-mvp-우선) **2단계**의 코드.
> "완벽한 전사본을 줬을 때 Claude 모순감지가 얼마나 맞히는가."

## 왜 이게 필요한가

[1단계(STT 벤치마크)](../stt/README.md)는 "받아쓰기가 정확한가"를 잰다. 2단계는 그 위층 —
**전사가 완벽하다고 가정**하고, Claude가 흐름단절 4종(모순·번복·미해결·재논의)을 얼마나
정확히 짚는지를 잰다. 두 실패모드를 **분리**해서:

- **가짜 감지**(false positive) — 없는 흐름단절을 지어냄. 사용자 신뢰를 깨는 지뢰.
- **놓친 감지**(miss) — 실제 흐름단절을 못 잡음. 제품 핵심 가치가 새는 구멍.

1단계의 CTER(sub=가짜모순 / del=놓친모순 분리)와 같은 철학을 감지층에 적용한 것이다.

## 채점 방식 — 객체탐지식 매칭

각 flag을 그 statement 인용이 grounding되는 **전사 세그먼트 집합**으로 대표하고,
예측 flag ↔ 골든 flag를 **(같은 type + 세그먼트집합 Jaccard ≥ 0.5)**로 그리디 1:1 매칭한다.

| 결과 | 뜻 | 제품 의미 |
|---|---|---|
| 매칭 | TP | 정타 (일부 인용이 할루시면 🟠 `tainted_matches`로 별도 노출) |
| 미매칭 골든 | FN | **놓친 모순** |
| 미매칭 예측 | FP | **가짜 모순** (할루시 인용 / 근거 인용 없음 / 골든에 없음 3분리) |

**quote grounding**은 이중 역할: ① Claude가 지어낸 인용(전사에 없음)을 잡아 드롭 ②
인용이 가리키는 세그먼트를 해소해 매칭 키로 사용. **type-무관 localization**을 따로 매겨,
흐름단절은 찾았는데 라벨만 틀린 경우(모순↔번복 혼동)를 `type_confusion`으로 분리 노출한다.

## 무엇이 들어있나 (순수·결정적 측정 코어, 크레덴셜 0)

```
detect_bench/
  labels.py     골든/예측 데이터 모델 + 로더 + 검증 게이트(전사↔flag 양방향 일관성)
  grounding.py  quote grounding — NFC 부분일치 + 토큰 Jaccard 폴백 (할루시 드롭 + 세그먼트 해소)
  score.py      감지 채점기 — 그리디 매칭 · per-type P/R/F1 · 가짜/놓친 분리 · type_confusion
  report.py     회의 단위 마크다운 리포트 + CLI
  detect.py     감지 어댑터 — 전사 → 프롬프트 → 감지 포트(리플레이/Claude) → 응답 파싱 → pred flags
  cliutil.py    CLI 공용 유틸 — force_utf8_stdio (T-027 단일 출처)
fixtures/
  golden/luma_meeting.json          골든 회의 1건 (기준선, 전사 25세그 + 4유형 한 건씩) — docs/data-schema.json 재사용
  golden/greenmart_meeting.json     골든 회의 2건 (하드케이스, 전사 26세그 + flag 6종) — 중첩·반복발화·모순↔번복 근접
  golden/payments_postmortem.json   골든 회의 3건 (하드케이스, 전사 27세그 + flag 5종) — 인접 동일화자 STT 분할로 경계 span·tier2를 채점 경로에서 스트레스
  pred/*.faithful.json              mock 예측: 완벽 재현 (전부 합성, 실제 API 아님)
  pred/*.contaminated.json          mock 예측: 실패모드 심음 (오타입·할루시·놓친·tainted)
  response/*.claude.txt             캔드 Claude 응답(리플레이용) — 크레덴셜 없이 어댑터 관통 검증
measurements/   실제 Claude(Opus 4.8) 실호출 감지 결과(동결 스냅샷) + 종합표·해석 — fixtures와 달리 실 API 산출
  *.pred.json                       골든 3건 실측 pred (2026-07-13, 재채점은 결정적·크레덴셜 0)
tests/          229개 테스트 (스키마·grounding·매칭·실패모드 분리·리포트·어댑터·하드케이스·실측 회귀 + 적대적 리뷰)
```

## 감지 어댑터 (전사 → pred JSON)

채점기의 *앞단*. 지금까지 pred JSON을 mock 픽스처로 대체했지만, `detect.py`가 실제로
그 pred를 만든다: 골든 전사 → 프롬프트(정답 누출 0) → 감지 포트 → Claude 자유형식 응답에서
flags JSON 견고 추출 → pred flag JSON. **런타임 의존성 0**(실제 Claude 포트도 anthropic SDK가
아니라 stdlib urllib) · 크레덴셜은 오직 실제 API 포트에만 게이트.

- **`ReplayDetectorPort`** — 캔드 응답 재생. 크레덴셜 없이 프롬프트→파싱→채점 **전 파이프라인**을
  실제로 관통(Track A의 톤 렌더러에 대응).
- **`ClaudeDetectorPort`** — 실제 Anthropic Messages API(stdlib HTTP). `ANTHROPIC_API_KEY` 없으면
  `DetectorCredentialError`. 크레덴셜 오면 포트만 스왑.

## 실행

```bash
cd benchmarks/detection
python -m pytest -q

# 감지 채점 리포트 (mock 예측 — 크레덴셜 없이 채점기 자체를 end-to-end 검증)
python -m detect_bench.report \
  --golden fixtures/golden/luma_meeting.json \
  --pred   fixtures/pred/luma_meeting.contaminated.json

# 어댑터 관통 (전사 → 감지 → pred JSON) — 리플레이 포트로 크레덴셜 없이
python -m detect_bench.detect \
  --golden   fixtures/golden/luma_meeting.json \
  --detector replay \
  --response fixtures/response/luma_meeting.claude.txt \
  --out      /tmp/pred.json
python -m detect_bench.report --golden fixtures/golden/luma_meeting.json --pred /tmp/pred.json

# 실제 Claude 감지 (크레덴셜 필요 — 어댑터는 동일, 포트만 스왑)
#   ANTHROPIC_API_KEY=... python -m detect_bench.detect \
#     --golden fixtures/golden/luma_meeting.json --detector claude --out pred.json
#   (응답이 max_tokens로 절단되면 클린 에러 — --max-tokens 를 올려 재시도)
```

## 스코프 경계 — 크레덴셜/다음 단계로 미룬 것

- ✅ **실제 Claude API 실호출** — 완료(PR #14). 골든 3건을 `--detector claude`(Opus 4.8)로 실감지 →
  **종합 정밀도 1.00 · 재현율 0.87**(`measurements/README.md`). 실 pred는 `measurements/`에 동결,
  채점 회귀는 `test_measured_real.py`가 실 API 재호출 없이 고정.
- **경계 span·tier2 gap 재설계** — 골든 3건째(`payments_postmortem.json`)가 두 gap을 현행 동작으로 스트레스·pin했다: ① 모호성 정책 비대칭(단일=첫출현 추측 vs 스팬=거부) ② 경계 퍼지 tier 부재(창 verbatim 전용 → 경계 인용 1단어 의역이면 전량 소실). 둘 다 매칭 의미론 변경이라 **실측 데이터 확보 후 재설계**(test_gap1/gap2가 재설계 시 알림 역할).
- **통계 판정층** — 1단계와 공유(clustered bootstrap CI 등). 다중 회의 수집 후.

> 골든 2건째(`greenmart_meeting.json`)는 **하드케이스** — 중첩(한 라인 2 flag)·반복발화 분해(디코이 vs 근거 time 갈림)·모순↔번복 근접(type_confusion)·교차화자 near-miss·같은 type 복수. judge panel로 선정.
> 골든 3건째(`payments_postmortem.json`)는 **인접 동일화자 세그먼트(STT 분할 모사)**로 경계 span grounding·tier2 퍼지를 **채점 경로에서** 스트레스한 첫 골든 — f1 첫 진술이 s6·s7에 쪼개진 경계 인용을 예측은 하나로 내고(span 회수) 골든은 세그먼트별로 라벨해 같은 segset로 매칭. 5R 보류 2건 gap을 현행 동작 pin.

## ⚠️ 데이터 정직성

`fixtures/pred/`의 예측과 `fixtures/response/`의 Claude 응답은 **전부 합성 mock**이다. 실제
Claude API 호출 결과가 아니며, 어떤 감지 성능도 주장하지 않는다. 채점기·어댑터의 정확성
(가짜/놓친/타입혼동 분리, 파싱 견고성)을 크레덴셜 없이 검증하기 위한 통제된 입력일 뿐이다.

반면 `measurements/`의 pred는 **실제 Claude API 실호출 산출물**(출처·모델·일자 명기)이다 —
mock과 디렉터리·README로 명확히 분리한다. **감지 성능 주장은 오직 `measurements/`에서만**,
출처를 밝히고 한다.

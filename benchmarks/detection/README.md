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
fixtures/
  golden/luma_meeting.json          골든 회의 1건 (전사 25세그 + flag 4종) — docs/data-schema.json 재사용
  pred/luma_meeting.faithful.json   mock 예측: 완벽 재현 (전부 합성, 실제 API 아님)
  pred/luma_meeting.contaminated.json  mock 예측: 4가지 실패모드 심음
tests/          46개 테스트 (스키마·grounding·매칭·실패모드 분리·리포트 + 적대적 리뷰 1·2차 회귀)
```

## 실행

```bash
cd benchmarks/detection
python -m pytest -q

# 감지 채점 리포트 (mock 예측 — 크레덴셜 없이 채점기 자체를 end-to-end 검증)
python -m detect_bench.report \
  --golden fixtures/golden/luma_meeting.json \
  --pred   fixtures/pred/luma_meeting.contaminated.json
```

## 스코프 경계 — 크레덴셜/다음 단계로 미룬 것

- **실제 Claude 감지 적용** — 지금은 mock 예측으로 채점기를 검증. 실제로는 전사본을
  Claude API에 넣어 flag JSON을 받아 이 채점기에 통과. (Claude API 크레덴셜 대기)
- **골든 회의 2건째** — 하드케이스(중첩 모순, 화자 혼동) 보강용.
- **통계 판정층** — 1단계와 공유(clustered bootstrap CI 등). 다중 회의 수집 후.

## ⚠️ 데이터 정직성

`fixtures/pred/`의 예측은 **합성 mock**이다. 실제 Claude API 호출 결과가 아니며, 어떤
감지 성능도 주장하지 않는다. 채점기의 정확성(가짜/놓친/타입혼동 분리)을 검증하기 위한
통제된 입력일 뿐이다.

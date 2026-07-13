# 실측 — 실제 Claude 감지 결과 (동결 스냅샷)

> **이 디렉터리는 `fixtures/`와 다르다.** `fixtures/pred/`·`fixtures/response/`는 전부 합성 mock
> (채점기·어댑터를 크레덴셜 없이 검증하는 통제 입력)이지만, 여기 `*.pred.json`은 **실제
> Anthropic API 실호출** 산출물이다.

## 출처 (provenance)

| 항목 | 값 |
|---|---|
| 모델 | `claude-opus-4-8` |
| 일자 | 2026-07-13 (KST) |
| 경로 | `detect_bench.detect --detector claude` (max_tokens 4096, stdlib HTTP) |
| 입력 | `fixtures/golden/*.json`의 **전사만** (골든 flag/summary는 프롬프트에 미노출) |

각 회의당 **API 호출 1회**(전사 → flags JSON). 채점(`report.py`)은 순수 로컬 — 호출 0.

## 종합 실측표

| 회의 | 성격 | 세그 | 골든 | TP | FP | FN | 정밀도 | 재현율 | F1 |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| luma | 기준선 | 25 | 4 | 4 | 0 | 0 | **1.00** | **1.00** | **1.00** |
| greenmart | 이탈대응 하드 | 26 | 6 | 5 | 0 | 1 | **1.00** | 0.83 | 0.91 |
| payments | 경계 span·tier2 | 27 | 5 | 4 | 0 | 1 | **1.00** | 0.80 | 0.89 |
| **합계** | | 78 | **15** | **13** | **0** | **2** | **1.00** | **0.87** | **0.93** |

## 해석 — 실패 모드가 한쪽으로 쏠려 있다

세 회의 모두 **정밀도 1.00**: Claude가 "흐름단절"이라 찍은 13건은 **전부 진짜**였다.
할루시 인용 0건, 타입 혼동 0건, 가짜 감지 0건. **놓친 2건은 전부 재현율**이고, 둘 다
**경계가 겹치는 애매한 케이스**다:

- **greenmart f3 (모순 놓침, {s11, s21})** — p4가 "무상연장 **반대**" → 나중에 "무상연장으로
  **가야**"로 자기모순. 그런데 Claude는 이 두 번째 발언을 **팀 결정 번복(번복)의 근거로 흡수**해,
  그룹 결정이 뒤집힌 서사로 읽고 p4 개인의 자기모순은 별도로 안 찍었다. **한 발언이 두 라벨
  (모순+번복)에 걸치는 중첩**을 하나로 뭉갠 것.
- **payments f3 (미해결 놓침, {s19})** — "다음 스프린트에 RCA 남기겠다"는 **미래 약속형** 미해결을
  '후속 있음 = 해결됨'으로 읽었다.

즉 실 감지기의 약점은 **과잉감지가 아니라 "겹치는 경계에서의 과소감지"**다 — 제품 관점에서
유리한 실패 방향(오탐으로 사용자를 괴롭히기보다 미묘한 중첩 단절을 놓치는 쪽).

## 재현 / 회귀

LLM 출력은 비결정적이라 실호출을 **그대로** 재현할 수는 없다(재호출하면 표현이 조금 달라질 수
있음). 그래서 이 pred는 **동결 스냅샷**이다. 반면 채점기는 순수·결정적이므로, 동결 pred를
골든으로 다시 채점하면 항상 위 표가 나온다 — 이를 `tests/test_measured_real.py`가 고정한다
(grounding/score가 바뀌어 **동일 실측 입력의 채점이 달라지면** 실 API 재호출 없이 잡는다).

```bash
cd benchmarks/detection
# 동결 실측 재채점 (크레덴셜 불요)
python -m detect_bench.report \
  --golden fixtures/golden/greenmart_meeting.json \
  --pred   measurements/greenmart_meeting.pred.json

# 새로 실호출해 갱신하려면 (ANTHROPIC_API_KEY 필요, 결과는 비결정적)
#   ANTHROPIC_API_KEY=... python -m detect_bench.detect \
#     --golden fixtures/golden/greenmart_meeting.json --detector claude \
#     --out measurements/greenmart_meeting.pred.json
```

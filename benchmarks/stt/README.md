# STT 벤치마크 하네스 — 클로바 vs AWS Transcribe (측정 코어)

> meeting-tracker [구축 순서](../../README.md#구축-순서-mvp-우선) **1단계**의 코드.
> "인프라를 짓기 전에 가장 약한 고리를 먼저 깬다."

## 왜 이게 먼저인가

이 제품의 헤드라인 기능(모순감지)은 **STT 정확도** 위에 얹혀 있다. STT가
"3천만원"을 "2천만원"으로 오인식하면, 모순감지 입력이 오염돼 두 가지 침묵형
실패가 난다:

- **가짜 모순**(false_contradiction) — 없는 모순을 지어낸다. 값이 그럴듯해서
  quote-grounding도 통과하는 지뢰.
- **놓친 모순**(missed_contradiction) — 발언이 삭제/손상돼 한쪽을 잃는다.

그래서 클로바 vs AWS Transcribe를 실측하기 전에, **무엇을 어떻게 잴지**부터
코드로 못박는다.

## 핵심 통찰 — 전체 CER은 이 제품의 지표가 아니다

전체 CER(문자오류율)은 필러 오인식과 "3천만원→2천만원"을 **같은 무게**로 센다.
그런데 제품 관점에선 전자는 무해하고 후자는 치명적이다. 데모로 보면:

| hypothesis | 전체 CER | AMOUNT CTER | 판정 |
|---|--:|--:|---|
| `삼천만원` (표면형만 다름, 값 등가) | > 0 | **0.00** | ✅ 무해 |
| `2천만원` (값 오인식) | **낮음(0.04)** | **1.00** | ❌ 가짜 모순 |

→ 1순위 KPI는 **CTER(Critical Token Error Rate)** — 금액·날짜·고유명사 같은
"모순감지에 치명적인 토큰"만 격리해 **값 등가**로 채점한다.

## 무엇이 들어있나 (PR1 = 순수·결정적 측정 코어, 크레덴셜·오디오 0)

```
stt_bench/
  normalize.py       NFC 강제 · 음절/자모 토크나이저 · N2 정규화(공백 collapse, N3 금지)
  cer.py             결정적 Levenshtein CER (raw/norm/jamo, outlier 플래그)
  korean_numbers.py  한국어 수 파서 — 한자어(만/억 폴딩)·고유어 수관형사·아라비아·범위/근사
  korean_datetime.py 날짜/시각 파서 — N월·주차·초중말, N시·반·N분
  entities.py        CriticalEntity/Segment 데이터 모델 (치명 토큰은 사람이 수동 주석)
  score.py           CTER 채점기 — 스팬 투영·값 등가 비교·sub/del 분리·모순 후보 수집
  golden.py          골든셋 로더 + 검증 게이트(NFC·오프셋·DATE/TIME 과소명세 차단)
  report.py          회의 단위 병합 + 마크다운 리포트 + CLI (세그먼트 조인 키 가드)
  synth.py           합성 골든 빌더 — 마크업 스크립트 하나 → 골든 JSON + TTS 매니페스트 (Track A)
fixtures/            데모 골든셋·모의 hypothesis + 합성 스크립트(synth/) — 전부 합성, 실측 아님
tests/               129개 테스트 (방법론 스펙 + 적대적 리뷰 회귀 F1~F13·R1~R15 + 합성 빌더·리뷰 회귀)
```

> 🔍 구현 후 다중 에이전트 적대적 코드리뷰를 **2라운드** 돌려, 한국어 파서·스팬
> 투영의 무성 실패를 잡아 전부 회귀로 고정했다 — 1라운드 F1~F13(13종; 예: '일'
> 날짜마커가 한자어 1과 충돌, 고유어 합성 수사 '열두=12' 미파싱, 과대 스팬이 값
> 반전을 삭제로 오분류), 2라운드 xhigh R1~R15(15종). `tests/test_regressions.py`,
> `tests/test_regressions_r.py` 참고.

## 실행

```bash
cd benchmarks/stt
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows (POSIX: .venv/bin/python)

# 테스트
.venv/Scripts/python -m pytest -q

# 데모 리포트 (모의 데이터 — 전체 CER은 낮은데 CTER은 100%인 걸 보여줌)
.venv/Scripts/python -m stt_bench.report \
  --golden fixtures/golden/budget_meeting.json \
  --hyp    fixtures/hyp/budget_meeting.aws_mock.json

# 합성 골든 + TTS 매니페스트 재생성 (Track A — 스크립트 하나에서 둘 다 파생)
.venv/Scripts/python -m stt_bench.synth \
  --script       fixtures/synth/budget_reversal.script.json \
  --out          fixtures/golden/synth_budget_reversal.json \
  --manifest-out fixtures/synth/budget_reversal.manifest.json
```

## 스코프 경계 — 다음 PR(v2)로 미룬 것

측정의 **타당성**을 위해 설계는 끝냈지만 이번 PR엔 넣지 않은 것들. 각각 왜
필요한지는 방법론 설계에서 도출:

- **통계 판정층** — clip 단위 clustered bootstrap(BCa) CI + McNemar 대응표본 검정 +
  사전등록 MDE. 엔티티를 i.i.d.로 보면 CI가 3~10배 과소추정돼 노이즈 위에서
  provider를 고른다. (다중 clip 수집 후)
- **STT 어댑터** — 클로바/Transcribe raw → 공통 `NormalizedTranscript`(word-level
  진실원) 매핑. Live/Replay 러너는 크레덴셜 뒤로 게이팅. 실제 API·오디오 도착 시.
- **화자귀속 지표** — 모순/번복은 "같은 사람"이 정의라, 값을 맞게 받아써도 화자를
  섞으면 오염. `critical_speaker_error` + DER.
- **역할스왑** — "지출 2천, 상한 3천"이 뒤바뀐 걸 `contradiction_key`로 검출.
- **프록시 실증** — value_mismatch가 실제 모순 오검출을 유발하는지, 2단계 Claude
  감지에 오류를 주입해 확인한 뒤에야 CRS 가중을 확정.
- **사전 공정성** — 클로바에만 사용자사전을 주면 불공정. dict_off/dict_on 분리 리포트.

## ⚠️ 데이터 정직성

`fixtures/`의 hypothesis는 **합성 데모 데이터**다. 실제 CLOVA/AWS API를 호출한
결과가 아니며, 어떤 provider 우열도 주장하지 않는다. 실측은 골든 오디오·크레덴셜이
준비된 뒤 위 v2 러너로 수행한다.

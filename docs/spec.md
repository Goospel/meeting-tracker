# 프로젝트 인계 문서 — 회의 녹음 → 인터랙티브 대시보드

> 이 문서는 Cowork(데스크톱 앱)에서 진행한 작업을 Claude Code에서 이어서 작업하기 위한
> 인계 노트입니다. 프로젝트 배경, 지금까지 만든 것, 데이터 구조, 남은 과제, 다음 단계를
> 모두 담았습니다. 작업 일자: 2026-07-11.

---

## 1. 프로젝트 목표

회의/토의 **녹음**을 **보기 좋은 인터랙티브 대시보드**로 자동 변환한다.

기존 상용 툴(Otter, Fireflies, 네이버 클로바노트 등)은 "녹음 → 텍스트 요약"에 머문다.
이 프로젝트의 **차별점이자 핵심 기능**은:

> **회의 중 참석자들이 자기/상대가 한 말을 까먹어서 흐름이 이상하게 틀어지는 지점을 잡아내는 것.**

즉 단순 요약이 아니라 **"흐름/모순 추적기(Flow & Contradiction Tracker)"** 가 메인이다.
감지 대상 4종:

| 유형 | 뜻 | 예시 |
|---|---|---|
| `모순` | 같은 사람이 앞뒤로 다른 말 | 예산 "3천만원" → 나중에 "2천만원" |
| `번복` | 확정했던 결정이 조용히 뒤집힘 | "8월 출시 확정" → 끝에 "9월" |
| `미해결` | 꺼내놓고 다시 안 다룬 안건 | "사전예약 이벤트 뒤에서 얘기하자" → 안 다룸 |
| `재논의` | 이견이 결론 없이 넘어감 | 기능 스펙 방향 이견 봉합 안 됨 |

## 2. 사용자가 정한 요구사항 (대화에서 확정)

- **입력**: 오디오/영상 파일 업로드 (mp3, m4a, mp4 등)
- **대시보드 내용**: ① 참석자별 발언 분석 ② 주제/타임라인 시각화
  ③ **흐름/모순 추적 (사용자가 특히 강조한 핵심)**
- **결과물**: 인터랙티브 HTML 대시보드
- **언어**: 한국어

## 3. 전체 파이프라인

```
① 녹음 파일 (mp3/m4a/mp4 ...)
        │  transcribe.py  (faster-whisper: 음성 → 타임스탬프 텍스트)
        ▼
② transcript.json   또는   Zoom/Teams/클로바노트 자막(.srt/.vtt/.txt)
        │  ← 분석 단계: 화자 정리 · 주제 구간 분할 · 모순/번복/미해결 감지
        │     (현재는 Claude가 transcript 를 읽고 analysis.json 을 생성)
        ▼
③ analysis.json   (data/sample.json 과 동일한 스키마)
        │  build_dashboard.py  (데이터 주입 + Chart.js 내장)
        ▼
④ dashboard.html   (단일 파일, 오프라인 100% 동작, 라이트/다크)
```

## 4. 파일 구성

| 파일 | 역할 | 상태 |
|---|---|---|
| `transcribe.py` | 오디오/영상 → 타임스탬프 전사 JSON (faster-whisper) | 작성 완료 (샌드박스에서 모델 다운로드 불가로 미실행) |
| `build_dashboard.py` | 분석 JSON → 단일 HTML (Chart.js 내장) | 완료·검증됨 |
| `template.html` | 대시보드 UI 템플릿 (라이트/다크, 반응형) | 완료·검증됨 |
| `data/sample.json` | 분석 JSON 예시 + 스키마 참고용 | 완료 |
| `vendor/chart.umd.js` | 내장용 Chart.js 4.4.1 | 포함 |
| `dashboard.html` | 샘플로 생성된 완성 대시보드 | 완료 (브라우저 확인됨) |
| `README.md` | 사용자용 사용 설명서 | 완료 |
| `HANDOFF.md` | 이 문서 (개발 인계 노트) | — |

## 5. 분석 JSON 스키마 (`data/sample.json` 참고)

```jsonc
{
  "meta": {
    "title", "date", "duration_sec", "location",
    "participants": [{ "id":"p1", "name", "role", "color" }]   // color 는 검증된 팔레트 사용
  },
  "summary": {
    "headline",                                    // 한 줄 핵심
    "overview",                                    // 문단 요약
    "decisions": [{ "text", "status":"확정|충돌" }],
    "action_items": [{ "task", "owner":"p1", "due":"YYYY-MM-DD", "done":false }]
  },
  "speaker_stats": [{ "id","talk_sec","turns","words","questions","interruptions" }],
  "topics": [{ "id","label","start_sec","end_sec","color","summary" }],
  "flags": [{                                      // ★ 핵심: 흐름/모순
    "id","type":"모순|번복|미해결|재논의","severity":"high|medium|low",
    "title","topic",
    "statements": [{ "speaker":"p2","time_sec","quote" }],  // 상충 발언(보통 2개)
    "explanation","resolution"
  }],
  "transcript": [{ "id","speaker":"p1","start_sec","text","topic_id","flags":["f1"] }]
}
```

- 참석자 색상은 dataviz 검증 팔레트(색맹 안전): `#2a78d6`(파랑) `#1baf7a`(청록)
  `#eda100`(노랑) `#008300`(초록) `#4a3aa7`(보라) `#e34948`(빨강).
- `flags[].statements` 에 2개를 넣으면 대시보드가 좌우로 나란히 비교(⇄)해 준다.

## 6. 대시보드 구성 (섹션)

1. 헤더 — 제목/일시/참석자 칩 + 다크모드 토글
2. KPI 4개 — 회의 길이 / 참석자 / 확정 결정 / **⚠️ 감지된 흐름 이슈 수(강조)**
3. **🧭 흐름 & 모순 추적기** — 타임라인 리본 + 번호 핀(클릭 시 해당 카드로 이동) +
   펼침 카드(상충 발언 좌우 비교, 설명, 처리방안)
4. 📋 한눈에 요약 — 결정사항(확정/충돌 뱃지) + 액션아이템 표
5. 🗣️ 참석자별 발언 분석 — 막대(발언시간)·도넛(비중) 차트 + 통계 카드
6. 🕒 주제 타임라인 — 주제별 구간 바
7. 📝 전체 스크립트 — 참석자 필터, 이슈 발언 하이라이트

## 7. 알려진 제약 / 주의사항

- **STT 모델 다운로드 차단**: Cowork 클라우드 샌드박스는 보안 정책상 HuggingFace/
  openaipublic 등에서 whisper 모델을 내려받지 못한다(403/차단 확인). 따라서 `transcribe.py`
  는 **인터넷이 되는 로컬 환경**(예: 본인 PC의 Claude Code)에서 실행해야 한다.
  → Claude Code 로컬에서는 정상 동작 예상.
- 대안: Zoom/Teams/클로바노트 등에서 이미 뽑은 자막(.srt/.vtt/.txt)이 있으면 STT 생략 가능.
- **화자 분리(diarization) 미구현**: `transcribe.py` 는 화자 라벨을 붙이지 않는다(faster-whisper
  단독). 현재는 전사본을 사람이/Claude가 읽고 화자를 배정. 자동화하려면 `pyannote.audio`
  (HF 토큰 필요) 또는 `whisperx` 통합 필요.
- **모순 감지 로직**: 현재 감지는 "Claude가 전사본을 읽고 판단 → analysis.json 작성" 방식.
  규칙 기반/LLM 기반 자동 감지 스크립트로 고도화하는 것이 다음 목표.

## 8. Claude Code에서 이어서 할 일 (제안)

- [ ] **로컬에서 `transcribe.py` 실제 실행 검증** — 실제 회의 녹음 1건으로 end-to-end 테스트
      (`pip install faster-whisper` 후 `python transcribe.py 파일.m4a --model small`).
- [ ] **화자 분리 추가** — `whisperx` 또는 `pyannote.audio` 로 speaker 라벨 자동화.
- [ ] **모순/번복 자동 감지 파이프라인** — transcript.json → LLM 호출(Claude API)로
      flags 자동 생성하는 `analyze.py` 작성. 지금은 이 단계가 수동(Claude 대화).
- [ ] **CLI 하나로 통합** — `python run.py 회의.m4a` → dashboard.html 까지 원샷.
- [ ] (선택) 감정 톤/키워드 추적, 발언 간 응답 관계(누가 누구 말에 반응했는지) 추가.
- [ ] (선택) 여러 회의 누적 대시보드 / 액션아이템 추적 보드.

## 9. 실행 방법 요약

```bash
# (로컬, 인터넷 필요) 음성 → 전사
pip install faster-whisper
python transcribe.py 회의녹음.m4a -o transcript.json --model small --language ko

# 전사본 분석 → analysis.json  (현재는 Claude가 수행 / 추후 analyze.py 로 자동화)

# 분석 JSON → 대시보드
python build_dashboard.py analysis.json -o dashboard.html
# dashboard.html 을 브라우저로 열면 끝
```

---
*생성: Claude (Cowork) · 이어서 작업: Claude Code*

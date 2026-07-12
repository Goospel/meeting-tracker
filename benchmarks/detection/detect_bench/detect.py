"""감지 어댑터 레이어 — 골든 전사 → 프롬프트 → 감지 포트 → 응답 파싱 → pred flags.

stage-2 채점기의 *앞단*이다. 채점기(score/report)는 pred flag JSON을 받아 채점하지만,
그 pred JSON을 **실제로 만들어내는** 부분이 지금까지 비어 있었다(mock 픽스처로 대체).
이 모듈이 그 앞단을 채운다:

  build_detection_prompt   전사만 제시(정답=골든 flag/summary는 절대 누출 안 함)
  parse_detection_response Claude 자유형식 출력에서 flags JSON을 견고하게 추출
  DetectorPort             감지 실행 추상화
    ReplayDetectorPort       캔드 응답 재생 — **크레덴셜 0으로 전 파이프라인 실검증**
    ClaudeDetectorPort       실제 Anthropic Messages API(stdlib HTTP) — 크레덴셜 게이트
  run_detection            전사 → (포트) → pred FlowFlag 리스트

Track A 렌더 레이어와 같은 패턴이다(Port + 크레덴셜-불요 실동작 구현 + 게이트 확장점).
순수 코어(프롬프트·파서)는 크레덴셜 0으로 전량 검증되고, 실제 API는 게이트 뒤에만 있다.
**런타임 의존성 0** — 실제 포트도 anthropic SDK가 아니라 stdlib urllib를 쓴다(크레덴셜만이 게이트).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .labels import load_meeting, pred_flags_from_items

_ENDPOINT = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_DEFAULT_MODEL = "claude-opus-4-8"
_DEFAULT_MAX_TOKENS = 4096

# 유형 정의(프롬프트용) — FlagType 주석과 같은 의미. 골든 라벨을 재서술한 게 아니라 '무엇을 찾을지' 지시.
_TYPE_GUIDE = [
    ("모순", "같은 사람이 회의 중 앞뒤로 상반된 말을 함 (수치·사실이 뒤집힘)"),
    ("번복", "확정했던 결정이 뒤에서 조용히 뒤집힘 (명시적 재결정 없이)"),
    ("미해결", "꺼내놓은 안건이 결론/후속 없이 다시 다뤄지지 않음"),
    ("재논의", "이견이 결론 없이 봉합되어 다음 주제로 넘어감"),
]


# ── 프롬프트 빌더 ──────────────────────────────────────────────────────────

def _render_participants(meta: dict) -> str:
    parts = meta.get("participants", [])
    if not isinstance(parts, list) or not parts:
        return ""
    lines = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        pid = p.get("id", "")
        name = p.get("name", "")
        role = p.get("role", "")
        tail = f" ({name}, {role})" if (name or role) else ""
        lines.append(f"- {pid}{tail}")
    return "참석자:\n" + "\n".join(lines) + "\n\n" if lines else ""


def _render_transcript(transcript: list) -> str:
    # 발화자 id + 초 + 텍스트. 세그먼트 역참조(seg.flags=골든 정답)는 렌더하지 않는다.
    return "\n".join(
        f"[{seg.start_sec:.0f}s] {seg.speaker}: {seg.text}" for seg in transcript
    )


def build_detection_prompt(meeting: dict) -> str:
    """골든 전사 → Claude 감지 프롬프트. **정답 누출 없음**.

    meeting = load_meeting 산출물({meta, transcript, flags, raw}). meta.title·participants·
    transcript만 쓴다 — meta.summary(결정 요약)·golden flags(제목/설명)는 정답이므로 절대
    넣지 않는다. statement의 speaker는 grounding 힌트로 쓰이므로, 발화자를 전사와 **같은 id**
    (p1, p2, ...)로 출력하도록 지시하고, quote는 전사본 **그대로**(verbatim)를 요구한다."""
    meta = meeting.get("meta", {}) or {}
    title = meta.get("title", "(제목 없음)")
    types = "\n".join(f"  - {name}: {desc}" for name, desc in _TYPE_GUIDE)
    # 예시 좌표는 **의도적 더미**(p1@0) — 실제 골든 flag 좌표를 앵커로 주면 감지 벤치가
    # 낙관 편향된다(모델이 예시를 답으로 베낄 위험). quote도 플레이스홀더라 grounding 안 됨.
    example = json.dumps(
        {"flags": [{
            "id": "1", "type": "모순",
            "statements": [
                {"speaker": "p1", "quote": "(전사에서 그대로 복사한 앞 발언)", "time_sec": 0},
                {"speaker": "p1", "quote": "(전사에서 그대로 복사한 상충 발언)", "time_sec": 0},
            ],
        }]},
        ensure_ascii=False, indent=2,
    )
    return f"""당신은 회의 전사를 분석해 **대화 흐름단절**을 찾는 도구입니다.

아래 전사에서 다음 4가지 유형의 흐름단절만 찾으세요:
{types}

## 출력 규칙 (엄격)
- 오직 JSON만 출력합니다. 서문/설명/코드펜스 없이 JSON 객체 하나만.
- 형식: {{"flags": [ {{ "id", "type", "statements": [ {{ "speaker", "quote", "time_sec" }} ] }} ]}}
- type 은 반드시 위 4개 한글 라벨 중 하나(모순/번복/미해결/재논의).
- quote 는 전사본에 있는 문장을 **글자 그대로**(verbatim) 복사합니다. 요약·의역 금지 —
  전사에 없는 인용은 근거로 인정되지 않습니다.
- speaker 는 전사에 쓰인 발화자 id(p1, p2, ...) 그대로.
- time_sec 은 그 발언이 나온 세그먼트의 초([...s]) 값.
- 흐름단절이 없으면 {{"flags": []}} 를 출력합니다.

## 예시 형식
{example}

## 회의: {title}
{_render_participants(meta)}## 전사
{_render_transcript(meeting["transcript"])}
"""


# ── 응답 파서 ──────────────────────────────────────────────────────────────

def _looks_like_flag(d) -> bool:
    """flag스러운 dict 판정 — statement(speaker/quote만 있음)나 산문 배열과 구분하기 위함.

    flag은 type/statements 키를 가진다. 이 판정으로 bare 배열/단일 오브젝트를 flags로 받을지
    거른다 → 응답 속 statements 배열·[1,2]·{speaker,quote} 배열이 진짜 flags를 강탈하는 것 차단."""
    return isinstance(d, dict) and ("type" in d or "statements" in d)


def parse_detection_response(raw: str) -> list:
    """Claude 자유형식 출력 → flag dict 리스트.

    Claude는 코드펜스(```json)·서문/후문 산문으로 JSON을 감싸 낼 수 있다. 문자열을 훑어 각
    '{'/'['에서 JSONDecoder.raw_decode로 JSON 값을 시도(후행 산문은 무시)하되, **컨테이너를
    위치가 아니라 의미로 고른다**:

      1순위 — 명시적 {"flags": [...]} 오브젝트. 여러 개면 **실제 flag(dict) 수가 최대**인 것,
              동수면 **뒤(마지막)**. 프롬프트가 형식/0건 예시({"flags": []})를 담으므로 Claude가
              그 형식을 서두에 되풀이(에코)하는데, "첫 컨테이너"를 잡으면 그 빈/예시 에코가 진짜
              답을 강탈한다(특히 빈 에코 → "0건 감지" 둔갑 = 벤치 오염, 리뷰 HIGH). '최대 flag 수'는
              에코(0~1건)를 진짜 답 위로 올리지 않는다.
      차선  — flags 컨테이너가 하나도 없을 때만: flag스러운 원소를 **하나라도** 가진 bare 배열
              (배열 전체를 강등 경로로 — all 게이트는 원소 하나 때문에 전체를 버려 부분손실을 냈다,
              리뷰 MED), 또는 래퍼 없는 단일 flag. 산문 속 [1,2]·{speaker,quote} 배열은 flag스러운
              원소가 없어 배제 → 무성 강탈 차단.

    - **빈 목록은 유효**: {"flags": []} → [] (Claude가 0건 감지). 추출 실패와 구분.
    - **추출 실패는 클린 에러**: 어떤 후보도 flags로 인정 안 되면 ValueError — '0건 감지'로 무성
      통과하면 벤치 비교가 오염된다(load_pred_flags의 전량-파싱-불가 가드와 같은 철학).
    """
    if not isinstance(raw, str):
        raise ValueError(f"Claude 응답이 문자열이 아님: {type(raw).__name__}")
    decoder = json.JSONDecoder()
    best_flags = None                                 # 채택된 flags 컨테이너
    best_n = -1                                        # 그 컨테이너의 실제 flag(dict) 수
    fallback = None                                   # flags 컨테이너 부재 시 차선(첫 것만)
    for i, ch in enumerate(raw):
        if ch not in "{[":
            continue
        try:
            obj, _ = decoder.raw_decode(raw, i)       # i가 값 시작('{'/'[')이라 선행 공백 문제 없음
        except ValueError:
            continue                                  # 이 '{'/'['에서 파싱 실패 → 다음 후보로
        if isinstance(obj, dict) and isinstance(obj.get("flags"), list):
            n = sum(1 for e in obj["flags"] if isinstance(e, dict))   # 패딩(비-dict)은 안 셈
            if n >= best_n:                           # >= : 동수면 뒤(마지막)를 채택
                best_flags, best_n = obj["flags"], n
            continue
        if fallback is not None:
            continue                                  # 차선은 첫 것만(flags 컨테이너는 위에서 계속 우선)
        if isinstance(obj, list) and any(_looks_like_flag(e) for e in obj):
            fallback = list(obj)                      # flag스러운 원소가 하나라도 있는 bare 배열
        elif _looks_like_flag(obj) and "flags" not in obj:
            fallback = [obj]                          # 래퍼 없는 단일 flag
    if best_flags is not None:
        return best_flags
    if fallback is not None:
        return fallback
    raise ValueError(
        "Claude 응답에서 flags JSON을 추출하지 못함 — 클린 에러(0건 감지 [] 와 구분)"
    )


# ── 감지 포트 ──────────────────────────────────────────────────────────────

class DetectorCredentialError(RuntimeError):
    """실제 Claude 감지에 필요한 API 크레덴셜이 없을 때 — TtsCredentialError와 같은 게이트."""


class DetectorPort:
    """감지 실행 추상화 — prompt → 원시 응답 텍스트."""

    def detect(self, prompt: str) -> str:              # pragma: no cover - 인터페이스
        raise NotImplementedError


class ReplayDetectorPort(DetectorPort):
    """캔드 응답 재생 — 크레덴셜 없이 프롬프트→파싱→채점 전 파이프라인을 실검증.

    Track A의 ToneTtsPort에 대응(실제 벤더 호출 없이 파이프라인을 '실제로' 관통). 프롬프트는
    무시하고 미리 저장된 원시 응답을 그대로 돌려준다 — 파서·채점기 end-to-end 검증용."""

    def __init__(self, response: str):
        if not isinstance(response, str):
            raise ValueError(f"replay 응답은 문자열이어야 함: {type(response).__name__}")
        self._response = response

    def detect(self, prompt: str) -> str:
        return self._response


class ClaudeDetectorPort(DetectorPort):
    """실제 Anthropic Messages API 호출 — stdlib urllib(런타임 의존성 0), 크레덴셜 게이트.

    api_key 없이 detect()하면 DetectorCredentialError. transport는 테스트 주입 seam
    (url, headers, body:bytes) -> bytes. 기본 transport는 urllib(키가 있을 때만 실호출)."""

    def __init__(self, api_key: str | None = None, *, model: str = _DEFAULT_MODEL,
                 max_tokens: int = _DEFAULT_MAX_TOKENS, transport=None):
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._transport = transport or _urllib_post

    def detect(self, prompt: str) -> str:
        if not self._api_key:
            raise DetectorCredentialError(
                "ANTHROPIC_API_KEY가 없습니다 — 실제 Claude 감지는 크레덴셜이 필요합니다 "
                "(크레덴셜 없이 검증하려면 --detector replay)."
            )
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }
        body = json.dumps({
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        return _text_from_api_response(self._transport(_ENDPOINT, headers, body))


def _urllib_post(url: str, headers: dict, body: bytes) -> bytes:
    """stdlib HTTP POST — 크레덴셜 있을 때만 호출된다(테스트는 transport 주입으로 우회).

    4xx/5xx는 urlopen이 본문 읽기 전에 HTTPError(OSError 서브클래스)를 raise한다 →
    본문의 에러 메시지(쿼터·인증 실패 등)를 읽어 클린 ValueError로 승격(그냥 두면 유용한
    진단이 소실되고 직접 호출자는 트레이스백을 맞는다 — 리뷰 MED)."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:      # noqa: S310 - 고정 Anthropic 엔드포인트
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:                              # noqa: BLE001 - 진단 최선노력
            pass
        raise ValueError(f"Claude API HTTP {exc.code}: {detail[:500]}") from exc


def _text_from_api_response(raw: bytes) -> str:
    """Anthropic 응답(bytes) → 텍스트. 에러/비정상 shape는 트레이스백 대신 클린 ValueError."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Claude API 응답이 JSON이 아님: {exc}")
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, list):
        msg = ""
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            msg = data["error"].get("message", "")
        raise ValueError(f"Claude API 응답에 content가 없음{(': ' + msg) if msg else ''}")
    text = "".join(
        b["text"] for b in content
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
    )
    if not text:                                       # 비문자열 text 블록도 여기로(join TypeError 방지)
        raise ValueError("Claude API 응답에 텍스트 블록이 없음")
    return text


def get_detector(name: str, *, response: str | None = None, api_key: str | None = None,
                 model: str = _DEFAULT_MODEL) -> DetectorPort:
    """감지기 팩토리 — 'replay'(크레덴셜 불요) | 'claude'(크레덴셜 게이트).

    Track A get_port와 같은 게이트: 크레덴셜 없는 claude 요청은 DetectorCredentialError."""
    if name == "replay":
        if response is None:
            raise ValueError("replay 감지기는 response(캔드 응답)가 필요합니다.")
        return ReplayDetectorPort(response)
    if name == "claude":
        if not api_key:
            raise DetectorCredentialError(
                "ANTHROPIC_API_KEY가 없습니다 — 실제 Claude 감지는 크레덴셜이 필요합니다."
            )
        return ClaudeDetectorPort(api_key=api_key, model=model)
    raise ValueError(f"알 수 없는 감지기: {name!r} (replay | claude)")


# ── 오케스트레이션 ──────────────────────────────────────────────────────────

def detect_items(meeting: dict, port: DetectorPort) -> list:
    """전사 → (포트) → 정규화된 flag dict 리스트(강등 전 원시 items)."""
    return parse_detection_response(port.detect(build_detection_prompt(meeting)))


def run_detection(meeting: dict, port: DetectorPort) -> list:
    """전사 → (포트) → pred FlowFlag 리스트(파일 로더와 같은 강등 규칙)."""
    return pred_flags_from_items(detect_items(meeting, port))


# ── CLI ────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    import argparse
    import os

    ap = argparse.ArgumentParser(
        description="감지 어댑터 — 골든 전사 → Claude 감지 → pred flag JSON (meeting-tracker 2단계)")
    ap.add_argument("--golden", required=True, help="골든/전사 회의 JSON 경로")
    ap.add_argument("--detector", choices=["replay", "claude"], default="replay",
                    help="replay=캔드 응답 재생(크레덴셜 불요) · claude=실제 API(ANTHROPIC_API_KEY)")
    ap.add_argument("--response", help="replay: 캔드 Claude 응답 텍스트 파일")
    ap.add_argument("--model", default=_DEFAULT_MODEL, help="claude: 모델 id")
    ap.add_argument("--out", help="pred flag JSON 출력 경로 — 없으면 stdout")
    a = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):            # Windows cp949 콘솔 회피 (T-027)
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    try:
        meeting = load_meeting(a.golden)
        if a.detector == "replay":
            if not a.response:
                raise ValueError("replay 감지기는 --response 파일이 필요합니다.")
            response = Path(a.response).read_text(encoding="utf-8-sig")
            port = get_detector("replay", response=response)
        else:
            port = get_detector("claude", api_key=os.environ.get("ANTHROPIC_API_KEY"),
                                model=a.model)
        items = detect_items(meeting, port)            # 추출 실패 → 클린 에러
        pred_flags_from_items(items)                   # 전량 파싱 불가 → 클린 에러(조기 검증)
    except (ValueError, OSError, DetectorCredentialError) as exc:
        print(f"감지 불가: {exc}", file=sys.stderr)
        return 2

    text = json.dumps({"flags": items}, ensure_ascii=False, indent=2)  # 원시 items 그대로(faithful)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {a.out} ({len(items)} flags)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

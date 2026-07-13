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
import math
import sys
from pathlib import Path

from .cliutil import force_utf8_stdio
from .labels import load_meeting, pred_flags_from_items

_ENDPOINT = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_DEFAULT_MODEL = "claude-opus-4-8"
_DEFAULT_MAX_TOKENS = 4096
_HTTP_TIMEOUT_SEC = 300                                # 스톨은 행이 아니라 클린 에러로 (3R P9)

# 크레덴셜 게이트 메시지 단일 출처 — 팩토리/직접 생성이 각자 문구를 들면 드리프트한다 (3R).
_NO_KEY_MSG = ("ANTHROPIC_API_KEY가 없습니다 — 실제 Claude 감지는 크레덴셜이 필요합니다 "
               "(크레덴셜 없이 검증하려면 --detector replay).")

# 유형 정의(프롬프트용) — FlagType 주석과 같은 의미. 골든 라벨을 재서술한 게 아니라 '무엇을 찾을지' 지시.
_TYPE_GUIDE = [
    ("모순", "같은 사람이 회의 중 앞뒤로 상반된 말을 함 (수치·사실이 뒤집힘)"),
    ("번복", "확정했던 결정이 뒤에서 조용히 뒤집힘 (명시적 재결정 없이)"),
    ("미해결", "꺼내놓은 안건이 결론/후속 없이 다시 다뤄지지 않음"),
    ("재논의", "이견이 결론 없이 봉합되어 다음 주제로 넘어감"),
]


# ── 프롬프트 빌더 ──────────────────────────────────────────────────────────

def _render_participants(meta: dict) -> str:
    parts = meta.get("participants")
    if not isinstance(parts, list):
        return ""
    lines = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        # `or ""` — 키가 명시적 null로 존재하면 dict.get 기본값이 안 먹어 'None'이 렌더된다 (3R P17)
        pid = p.get("id") or ""
        name = p.get("name") or ""
        role = p.get("role") or ""
        # name/role 중 있는 것만 조립 — 한쪽만 있을 때 '(, 영업)' 기형 방지 (3R SW3)
        detail = ", ".join(x for x in (name, role) if x)
        tail = f" ({detail})" if detail else ""
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
    meta = meeting["meta"]                             # load_meeting 산출물 계약 — 항상 dict(소스 게이트)
    title = meta.get("title") or "(제목 없음)"          # 명시적 null도 기본값으로 (3R P17)
    types = "\n".join(f"  - {name}: {desc}" for name, desc in _TYPE_GUIDE)
    # 예시는 **의도적으로 JSON 문법 밖 표기**(<...> 플레이스홀더, 좌표·verbatim 인용 없음) —
    # 파싱 가능한 예시는 모델이 에코할 때 파서의 추출 후보가 되어 진짜 답(특히 0건)을 강탈한다
    # (3R 뿌리 수정, T-032). 좌표를 안 넣는 것은 골든 정답 앵커 노출(낙관 편향) 방지이기도 하다.
    example = (
        '{"flags": [{\n'
        '  "id": "1", "type": "모순",\n'
        '  "statements": [\n'
        '    {"speaker": "p1", "quote": "(전사에서 그대로 복사한 앞 발언)", "time_sec": <그 발언의 초>},\n'
        '    {"speaker": "p1", "quote": "(전사에서 그대로 복사한 상충 발언)", "time_sec": <그 발언의 초>}\n'
        "  ]\n"
        "}]}"
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
- 흐름단절이 없으면 "flags" 를 빈 배열로 출력합니다.

## 예시 형식 (표기용 — <...> 자리는 실제 값으로 채움)
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
    '{'/'['에서 JSONDecoder.raw_decode로 JSON 값을 시도하되, **컨테이너를 위치가 아니라
    의미로 고른다** (T-031). 3R에서 극단 카디널리티(0건·1건·절단)의 구멍을 막은 규칙:

      1순위 — 명시적 {"flags": [...]} 오브젝트. 여러 개면 **flag스러운 원소 수 최대**(임의 dict가
              아니라 _looks_like_flag — statement dict로 채워진 기형 에코가 수로 못 이김), 동수면
              뒤(마지막). 단 **내용 0(빈/무내용) 컨테이너는 내용 있는 차선 후보에 양보** — bare
              배열 진짜 답 + 후행 규칙 에코 {"flags": []}에서 빈 에코가 강탈하는 것 방지.
      차선  — flags 컨테이너가 하나도 없을 때만: flag스러운 원소가 **가장 많은** bare 배열 또는
              래퍼 없는 단일 flag(첫 후보 고정이 아님 — 서두 에코 오브젝트가 뒤의 진짜 배열을
              강탈했었다). any 게이트라 비정상 원소가 섞여도 배열 전체가 강등 경로로 살아간다.
      절단 가드 — '"flags"' 래퍼 텍스트는 있는데 유효한 컨테이너가 하나도 파싱되지 않으면
              **클린 에러**(부분 조각 인양 금지 — 절단된 10건이 '1건 감지'로 둔갑하는 무성
              부분손실 차단). 래퍼 없는 bare 배열 답은 이 가드에 걸리지 않는다.

    - **빈 목록은 유효**: {"flags": []} → [] (0건 감지). bare '[]'는 의도적으로 추출 실패 —
      산문 속 stray '[]'와 구분 불가라 모호하면 fail-loud(파일 로더의 bare [] 수용과 다른 규칙,
      labels.coerce_pred_container 독스트링 참조).
    - **추출 실패는 클린 에러**: '0건 감지'로 무성 통과하면 벤치 비교가 오염된다.
    - 한계(정직): 프롬프트 예시는 JSON 문법 밖 표기라 **verbatim 에코는 후보가 못 되지만**,
      모델이 스스로 지어낸 '완성형 더미'는 어떤 파서도 진짜 답과 구분 불가 — grounding
      (전사 verbatim 대조)이 최종 방어선이다.
    """
    if not isinstance(raw, str):
        raise ValueError(f"Claude 응답이 문자열이 아님: {type(raw).__name__}")
    decoder = json.JSONDecoder()
    best_flags = None                                 # 채택된 flags 컨테이너
    best_n = -1                                        # 그 컨테이너의 flag스러운 원소 수
    fallback = None                                   # flags 컨테이너 부재 시 차선
    fallback_n = 0                                     # 차선의 flag스러운 원소 수(최다 후보 유지)
    for i, ch in enumerate(raw):
        if ch not in "{[":
            continue
        try:
            obj, _ = decoder.raw_decode(raw, i)       # i가 값 시작('{'/'[')이라 선행 공백 문제 없음
        except ValueError:
            continue                                  # 이 '{'/'['에서 파싱 실패 → 다음 후보로
        except RecursionError:
            continue                                  # 퇴화 중첩('['*N)도 이 위치만 포기 — 뒤의 유효
                                                      # 후보 탐색 지속(트레이스백 이스케이프 방지, 3R P5)
        if isinstance(obj, dict) and isinstance(obj.get("flags"), list):
            n = sum(1 for e in obj["flags"] if _looks_like_flag(e))
            if n >= best_n:                           # >= : 동수면 뒤(마지막)를 채택
                best_flags, best_n = obj["flags"], n
            continue
        if isinstance(obj, list):
            n = sum(1 for e in obj if _looks_like_flag(e))
            if n > fallback_n:                        # 내용 최다 후보 유지(빈 배열 n=0은 못 들어옴)
                fallback, fallback_n = list(obj), n
        elif _looks_like_flag(obj) and "flags" not in obj:
            if fallback_n < 1:                        # 단일 flag(n=1)은 더 큰 배열 후보를 못 이김
                fallback, fallback_n = [obj], 1
    if best_flags is not None:
        if best_n == 0 and fallback_n > 0:            # 빈/무내용 컨테이너 vs 내용 있는 후보 → 내용
            return fallback                           # (트레이드오프: 진짜 0건 + 자작 더미 배열이면
                                                      #  더미가 이기지만, 그 더미는 grounding에서 드롭)
        if best_flags and not any(isinstance(e, dict) for e in best_flags):
            raise ValueError(                         # 전량 비-dict = 전량 파싱 불가 — 파서 계약으로
                "flags 원소가 전부 dict가 아님 — 전량 파싱 불가 (0건 감지와 구분)")
        return best_flags
    if '"flags"' in raw:
        raise ValueError(
            '응답에 "flags" 래퍼 텍스트가 있으나 완전한 JSON으로 파싱되지 않음 — '
            "절단(max_tokens)/손상 의심. 부분 조각 인양은 무성 부분손실이라 거부")
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

    게이트는 **생성 시점 단일 지점**(3R) — detect()까지 미루면 팩토리/직접 생성이 각자
    게이트를 복제해 메시지가 드리프트한다(실재했던 분기). 공백만 든 키도 거부(strip) —
    falsy 게이트만 있으면 ' ' 키가 실 네트워크 401까지 가서 안내 메시지가 소실된다.
    transport는 테스트 주입 seam (url, headers, body:bytes) -> bytes."""

    def __init__(self, api_key: str | None = None, *, model: str = _DEFAULT_MODEL,
                 max_tokens: int = _DEFAULT_MAX_TOKENS, transport=None):
        if not api_key or not api_key.strip():
            raise DetectorCredentialError(_NO_KEY_MSG)
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._transport = transport or _urllib_post

    def detect(self, prompt: str) -> str:
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


def _urllib_post(url: str, headers: dict, body: bytes,
                 timeout: float = _HTTP_TIMEOUT_SEC) -> bytes:
    """stdlib HTTP POST — 크레덴셜 있을 때만 호출된다(테스트는 transport 주입으로 우회).

    4xx/5xx는 urlopen이 본문 읽기 전에 HTTPError(OSError 서브클래스)를 raise한다 →
    본문의 에러 메시지(쿼터·인증 실패 등)를 읽어 클린 ValueError로 승격(그냥 두면 유용한
    진단이 소실되고 직접 호출자는 트레이스백을 맞는다 — 리뷰 MED).
    timeout 필수(3R P9) — 소켓 기본은 무한이라 스톨 시 CLI가 출력 없이 영구 행.
    socket.timeout은 OSError 서브클래스라 CLI의 기존 클린 에러(rc=2) 경로에 그대로 잡힌다."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 고정 엔드포인트
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:                              # noqa: BLE001 - 진단 최선노력
            pass
        raise ValueError(f"Claude API HTTP {exc.code}: {detail[:500]}") from exc


def _text_from_api_response(raw: bytes) -> str:
    """Anthropic 응답(bytes) → 텍스트. 에러/비정상 shape는 트레이스백 대신 클린 ValueError.

    stop_reason=max_tokens는 **절단**이다(3R P6) — envelope은 정상 JSON이라 텍스트가 멀쩡해
    보여도, 잘린 flags JSON이 파서로 흘러가면 '10건 감지 → 1건 기록' 무성 부분손실이 된다.
    여기서 클린 에러로 잡아야 탐지 가능한 실패가 된다."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Claude API 응답이 JSON이 아님: {exc}")
    if isinstance(data, dict) and data.get("stop_reason") == "max_tokens":
        raise ValueError(
            "Claude 응답이 max_tokens로 절단됨 — 부분 JSON 채점은 벤치 오염이라 거부. "
            "--max-tokens 를 올려 재시도하세요.")
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, list):
        msg = ""
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            msg = data["error"].get("message", "")
        if not isinstance(msg, str):                   # 비표준 게이트웨이의 비문자열 message도
            msg = repr(msg)                            # 클린 에러로 (': '+dict TypeError 방지, 3R P8)
        raise ValueError(f"Claude API 응답에 content가 없음{(': ' + msg) if msg else ''}")
    text = "".join(
        b["text"] for b in content
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
    )
    if not text:                                       # 비문자열 text 블록도 여기로(join TypeError 방지)
        raise ValueError("Claude API 응답에 텍스트 블록이 없음")
    return text


def get_detector(name: str, *, response: str | None = None, api_key: str | None = None,
                 model: str = _DEFAULT_MODEL,
                 max_tokens: int = _DEFAULT_MAX_TOKENS) -> DetectorPort:
    """감지기 팩토리 — 'replay'(크레덴셜 불요) | 'claude'(크레덴셜 게이트).

    입력 검증은 각 포트 생성자 **단일 지점**에 위임한다(3R) — 팩토리가 게이트를 복제하면
    메시지가 드리프트한다(크레덴셜 안내 문구가 실제로 갈라져 있었다). max_tokens 포워딩(3R P7):
    절단(stop_reason=max_tokens) 클린 에러를 만났을 때 상한을 올릴 지원 경로가 있어야 한다."""
    if name == "replay":
        return ReplayDetectorPort(response)            # None/비문자열은 포트가 클린 ValueError
    if name == "claude":
        return ClaudeDetectorPort(api_key=api_key, model=model, max_tokens=max_tokens)
    raise ValueError(f"알 수 없는 감지기: {name!r} (replay | claude)")


# ── 오케스트레이션 ──────────────────────────────────────────────────────────

def detect_items(meeting: dict, port: DetectorPort) -> list:
    """전사 → (포트) → 정규화된 flag dict 리스트(강등 전 원시 items)."""
    return parse_detection_response(port.detect(build_detection_prompt(meeting)))


def run_detection(meeting: dict, port: DetectorPort) -> list:
    """전사 → (포트) → pred FlowFlag 리스트(파일 로더와 같은 강등 규칙)."""
    return pred_flags_from_items(detect_items(meeting, port))


# ── CLI ────────────────────────────────────────────────────────────────────

def _scrub_nonfinite(obj):
    """응답 JSON 속 NaN/±Infinity(파이썬 파서는 기본 허용)를 null로 강등 (3R P12).

    pred 파일은 표준(RFC 8259) JSON이어야 한다 — bare NaN이 박히면 파이썬 밖 소비자(jq 등)가
    파일째 파싱 실패. per-flag 강등 철학대로 좌표 하나 때문에 run을 죽이지 않는다(비유한
    time_sec은 어차피 grounding _num이 힌트에서 제외)."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, list):
        return [_scrub_nonfinite(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _scrub_nonfinite(v) for k, v in obj.items()}
    return obj


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
    ap.add_argument("--max-tokens", type=int, default=_DEFAULT_MAX_TOKENS,
                    help="claude: 응답 상한 토큰 — 절단 클린 에러 시 올려서 재시도 (3R P7)")
    ap.add_argument("--out", help="pred flag JSON 출력 경로 — 없으면 stdout")
    a = ap.parse_args(argv)

    force_utf8_stdio()                                 # Windows cp949 콘솔 회피 (T-027, 공용 헬퍼)

    try:
        meeting = load_meeting(a.golden)
        if a.detector == "replay":
            if not a.response:
                raise ValueError("replay 감지기는 --response 파일이 필요합니다.")
            response = Path(a.response).read_text(encoding="utf-8-sig")
            port = get_detector("replay", response=response)
        else:
            port = get_detector("claude", api_key=os.environ.get("ANTHROPIC_API_KEY"),
                                model=a.model, max_tokens=a.max_tokens)
        # 추출 실패/전량 파싱 불가는 파서 자신의 계약(클린 에러) — CLI의 별도 조기 검증 불요(3R).
        items = _scrub_nonfinite(detect_items(meeting, port))
        text = json.dumps({"flags": items}, ensure_ascii=False, indent=2,
                          allow_nan=False)             # 원시 items 그대로(faithful) + 표준 JSON 보증
        # 출력 쓰기도 try 안 — 쓰기 OSError만 트레이스백으로 새는 비대칭 제거 (3R P11).
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(text + "\n", encoding="utf-8")
            print(f"wrote {a.out} ({len(items)} flags)")
        else:
            print(text)
    except (ValueError, OSError, DetectorCredentialError) as exc:
        print(f"감지 불가: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

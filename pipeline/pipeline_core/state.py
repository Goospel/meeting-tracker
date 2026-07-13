"""오케스트레이터 상태머신 — 순수 결정적 코어(크레덴셜 0·IO 0·부작용 0).

meeting-tracker 파이프라인(업로드→STT→분석→저장)의 심장. 잡의 상태·전이·불변식·멱등성·
실패/재시도를 여기서만 정의하고, 영속화(PR2)·포트(PR3)·오케스트레이션(PR4)은 바깥에서
이 코어를 구동한다. README "상태머신 = 단일 진실원"의 코드 이행.

## 비동기 형상 (되돌리기 비싼 구조 결정 — 지금 못박음)
실 STT(클로바)는 **비동기**다: 잡 제출 후 웹훅 콜백으로 전사가 도착한다. 그래서 SUBMIT_STT는
TRANSCRIBING("제출됨·콜백 대기")에 머물고, 별도 STT_CALLBACK 이벤트가 와야 TRANSCRIBED가
된다. 동기(제출=즉시 전사)로 설계하면 실 클로바 전환 시 오케스트레이터 전면 재작성이므로,
실동작(HMAC 웹훅 등)은 미루되 이 '형상'만 선반영한다.

## 정확히-한번을 위한 ANALYZED 중간 상태
감지 결과가 '산출됨(ANALYZED)'과 '커밋됨(DONE)'을 분리한다. 크래시 후 재개(PR5) 시 ANALYZED로
영속된 잡은 감지를 다시 하지 않고 COMMIT만 재개 → 유료 감지 이중호출 차단.

## 멱등 규칙 (at-least-once 흡수)
전진 이벤트는 두 경우에 원본 job 그대로 no-op이다: (a) 비터미널 happy-path에서 이미 도달/경과한
target(예: TRANSCRIBED에 중복 STT_CALLBACK), (b) 터미널 상태를 **만든 바로 그 이벤트**의 중복
(예: DONE에 재-COMMIT). 그 외 터미널에서의 이벤트, 그리고 단계를 건너뛰는 전진은 fail-loud.

## 필수 ref 강제
STT_CALLBACK 실 전이는 transcript_ref, ANALYSIS_DONE 실 전이는 result_ref가 필수다(None이면
IllegalTransition). "TRANSCRIBED=전사 있음"·"ANALYZED=결과 있음" 불변식이 조용히 깨지면,
재-STT 회피 재개가 빈 전사 위에서 감지를 재실행한다(리뷰 확정 결함).

## 상태 정규화
Job은 __post_init__에서 문자열 state/failed_from을 State enum으로 정규화한다. 코어는 전부
identity(`is`)로 판정하므로, PR2가 sqlite에서 문자열로 rehydrate해도 재개 경로가 깨지지 않는다.

## RETRYING 상태를 두지 않은 이유 (altitude)
설계안엔 RETRYING이 있었으나, RETRY를 '단일 이벤트로 마지막 체크포인트에서 재개'로 정의하면
(FAILED→resume_point 직행) RETRYING은 도달 불가한 의례 상태가 된다. 그 '백오프 대기' 의미는
타이밍이 생기는 오케스트레이터(PR5) 전엔 쓰이지 않으므로, 순수 코어의 불변식을 선명히 유지하려
지금은 두지 않는다(YAGNI). 필요해지면 그때 도입한다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

DEFAULT_MAX_ATTEMPTS = 3


class State(str, Enum):
    UPLOADED = "uploaded"
    TRANSCRIBING = "transcribing"          # STT 제출됨 · 콜백 대기 (비동기 형상)
    TRANSCRIBED = "transcribed"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"                  # 감지 산출됨 · 커밋 전 (정확히-한번 seam)
    DONE = "done"                          # terminal(성공)
    FAILED = "failed"                      # 복구 가능 — failed_from 스테이지 재개 대상
    PERMANENTLY_FAILED = "permanently_failed"   # terminal(재시도 소진)


class Event(str, Enum):
    SUBMIT_STT = "submit_stt"
    STT_CALLBACK = "stt_callback"
    START_ANALYSIS = "start_analysis"
    ANALYSIS_DONE = "analysis_done"
    COMMIT = "commit"
    FAIL = "fail"
    RETRY = "retry"


class IllegalTransition(ValueError):
    """(state, event)가 전이표에 없거나 필수 데이터가 없음 — 무성 전진 금지, fail-loud."""


# 정상 전진 이벤트: event -> (source, target). 각 이벤트는 정확히 한 전진 간선.
_FORWARD = {
    Event.SUBMIT_STT: (State.UPLOADED, State.TRANSCRIBING),
    Event.STT_CALLBACK: (State.TRANSCRIBING, State.TRANSCRIBED),
    Event.START_ANALYSIS: (State.TRANSCRIBED, State.ANALYZING),
    Event.ANALYSIS_DONE: (State.ANALYZING, State.ANALYZED),
    Event.COMMIT: (State.ANALYZED, State.DONE),
}

# happy-path 선형 순서 — 멱등 판정(이미 지난 단계의 전진 이벤트 = no-op)에 쓴다.
_HAPPY_ORDER = (
    State.UPLOADED, State.TRANSCRIBING, State.TRANSCRIBED,
    State.ANALYZING, State.ANALYZED, State.DONE,
)
_ORDER = {s: i for i, s in enumerate(_HAPPY_ORDER)}

_TERMINAL = frozenset({State.DONE, State.PERMANENTLY_FAILED})

# 실패 가능한(외부 작업) 스테이지 -> 재개 체크포인트(그 스테이지의 입력 상태).
# ANALYZING 실패는 TRANSCRIBED부터 재개(전사 이미 지불 → 재-STT 비용 회피).
_RESUME_POINT = {
    State.TRANSCRIBING: State.UPLOADED,
    State.ANALYZING: State.TRANSCRIBED,
}


@dataclass(frozen=True)
class Job:
    """파이프라인 잡의 불변 스냅샷. advance()는 새 Job을 반환한다(제자리 변경 없음).

    __post_init__이 문자열 state/failed_from을 State enum으로 정규화한다 — PR2가 sqlite에서
    문자열로 rehydrate해도 코어의 identity 비교(`is`)가 성립하게(리뷰 확정 지뢰 방어).
    """

    id: str
    state: State
    audio_ref: str
    transcript_ref: str | None = None
    result_ref: str | None = None
    failed_from: State | None = None       # FAILED일 때 어느 스테이지가 실패했는가
    attempts: int = 0                      # 누적 실패 횟수(재시도 예산 판정)
    error: str | None = None
    ts: str | None = None                  # 마지막 전이 시각(주입 Clock)

    def __post_init__(self):
        # 문자열 → State 정규화(알 수 없는 값은 State(...)가 ValueError로 fail-loud).
        if not isinstance(self.state, State):
            object.__setattr__(self, "state", State(self.state))
        if self.failed_from is not None and not isinstance(self.failed_from, State):
            object.__setattr__(self, "failed_from", State(self.failed_from))


# ── 결정성 seam: Clock / IdSource ────────────────────────────────────
class Clock:
    def now(self) -> str:                  # pragma: no cover - 인터페이스
        raise NotImplementedError


class SystemClock(Clock):
    def now(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()


class FixedClock(Clock):
    """테스트용 — 항상 같은 값(결정적 ts)."""

    def __init__(self, value: str):
        self._value = value

    def now(self) -> str:
        return self._value


class IdSource:
    def new_id(self) -> str:               # pragma: no cover - 인터페이스
        raise NotImplementedError


class UuidIdSource(IdSource):
    def new_id(self) -> str:
        import uuid

        return uuid.uuid4().hex


class SequentialIdSource(IdSource):
    """테스트용 — prefix-1, prefix-2, ... 결정적."""

    def __init__(self, prefix: str = "job"):
        self._prefix = prefix
        self._n = 0

    def new_id(self) -> str:
        self._n += 1
        return f"{self._prefix}-{self._n}"


# ── 전이 ──────────────────────────────────────────────────────────────
def new_job(audio_ref: str, *, clock: Clock, ids: IdSource) -> Job:
    """UPLOADED 잡 생성 — id·ts는 주입된 IdSource·Clock에서(결정성 seam)."""
    return Job(id=ids.new_id(), state=State.UPLOADED, audio_ref=audio_ref,
               attempts=0, ts=clock.now())


def advance(job: Job, event: Event, *, clock: Clock,
            transcript_ref: str | None = None, result_ref: str | None = None,
            error: str | None = None,
            max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> Job:
    """(job, event) → 새 Job. 불법 전이·필수 데이터 누락은 IllegalTransition(fail-loud).

    순수 함수 — 부작용 0, 입력은 전부 인자로, 반환은 frozen replace(새 인스턴스).
    멱등: 이미 지난 단계의 전진 이벤트(중복 콜백/제출)와 터미널을 만든 이벤트의 중복은 no-op.
    """
    if not isinstance(event, Event):       # 문자열 이벤트도 정규화(identity 비교 전제)
        event = Event(event)

    # 1) 터미널: 그 상태를 만든 이벤트의 중복만 멱등 no-op, 나머지는 전부 거부.
    if job.state in _TERMINAL:
        fwd = _FORWARD.get(event)
        if fwd is not None and fwd[1] is job.state:
            return job                     # 예: DONE + COMMIT(재전달) → no-op(멱등)
        raise IllegalTransition(f"터미널 상태 {job.state.value}에서 {event.value} 불가")

    # 2) 실패 — TRANSCRIBING/ANALYZING(외부 작업 단계)에서만.
    if event is Event.FAIL:
        if job.state not in _RESUME_POINT:
            raise IllegalTransition(
                f"{job.state.value}에서 FAIL 불가(외부 작업 단계 아님)")
        return replace(job, state=State.FAILED, failed_from=job.state,
                       attempts=job.attempts + 1, error=error, ts=clock.now())

    # 3) 재시도 — 예산 남으면 체크포인트 재개, 소진이면 영구 실패.
    if event is Event.RETRY:
        if job.state is not State.FAILED:
            raise IllegalTransition(f"{job.state.value}에서 RETRY 불가(FAILED 아님)")
        if job.attempts < max_attempts:
            resume = _RESUME_POINT.get(job.failed_from)
            if resume is None:             # 정상 FAILED엔 항상 failed_from이 있으나 방어
                raise IllegalTransition(
                    f"failed_from={job.failed_from}에서 재개 지점 없음")
            return replace(job, state=resume, failed_from=None, error=None,
                           ts=clock.now())
        return replace(job, state=State.PERMANENTLY_FAILED, ts=clock.now())

    # 4) 전진 이벤트.
    if event in _FORWARD:
        src, tgt = _FORWARD[event]
        if job.state is src:
            # 필수 ref 강제 — 전사/결과 없는 전진은 불변식을 깨므로 fail-loud.
            if event is Event.STT_CALLBACK and transcript_ref is None:
                raise IllegalTransition(
                    "STT_CALLBACK엔 transcript_ref가 필요합니다(전사 없는 전진 금지)")
            if event is Event.ANALYSIS_DONE and result_ref is None:
                raise IllegalTransition(
                    "ANALYSIS_DONE엔 result_ref가 필요합니다(결과 없는 전진 금지)")
            changes = {"state": tgt, "ts": clock.now()}
            if event is Event.STT_CALLBACK:
                changes["transcript_ref"] = transcript_ref
            if event is Event.ANALYSIS_DONE:
                changes["result_ref"] = result_ref
            return replace(job, **changes)
        # 멱등: 이미 target에 도달했거나 그 이후면 no-op(중복 콜백/제출, 퇴행 금지).
        if job.state in _ORDER:
            if _ORDER[job.state] >= _ORDER[tgt]:
                return job
            raise IllegalTransition(
                f"{job.state.value}에서 {event.value} 불가(단계 건너뜀)")
        # 비-happy·비-터미널 = FAILED — 전진 대신 RETRY로 재개해야 한다.
        raise IllegalTransition(
            f"{job.state.value} 상태에서 {event.value} 불가 — RETRY로 재개하세요")

    raise IllegalTransition(f"알 수 없는 이벤트: {event}")   # pragma: no cover

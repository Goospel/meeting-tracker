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
target(예: TRANSCRIBED에 중복 STT_CALLBACK), (b) 터미널 상태를 **만든 바로 그 이벤트**의 중복.
(b)는 두 터미널에 대칭 적용된다(_TERMINAL_MAKER) — DONE←COMMIT 재전달, PERMANENTLY_FAILED←RETRY
재전달 모두 no-op. 그 외 터미널에서의 이벤트, 그리고 단계를 건너뛰는 전진은 fail-loud.

## 필수 ref 강제 (전진 인자 + 상태 진입 불변식)
두 층으로 "TRANSCRIBED=전사 있음"·"ANALYZED=결과 있음"을 강제한다.
- **전진 인자**: STT_CALLBACK 실 전이는 transcript_ref, ANALYSIS_DONE 실 전이는 result_ref가
  필수(None이면 IllegalTransition) — '전이로 쓰는 값'을 검사.
- **상태 진입 불변식**(_STATE_REQUIRES): advance()가 모든 진입점(현재 상태)과 재개 결과에서
  _check_state_invariant로 잡의 '현재 값'을 재확인. 손상 rehydrate가 빈 전사/결과 잡을 밀어넣어도
  재-STT 회피 재개나 COMMIT(빈 결과→DONE), 빈 전사 위 START_ANALYSIS가 진행하지 못하게 균일 차단.
조용히 깨지면 재-STT 회피 재개가 빈 전사 위에서 감지를 재실행한다(리뷰 확정 결함).

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


class CorruptJob(ValueError):
    """영속본이 상태 모델상 존재할 수 없는 잡 — 로드/저장 경계(PR2 rehydrate)에서 fail-loud.

    advance()는 이런 잡을 만들지 않는다(순수 코어의 전이는 계보를 항상 보존). 오직 외부
    rehydrate(sqlite·수동 편집·스키마 드리프트·다른 라이터)만 계보 끊긴 잡을 밀어넣을 수 있고,
    validate_persisted가 그 경계에서 검출한다."""


# 정상 전진 이벤트: event -> (source, target). 각 이벤트는 정확히 한 전진 간선.
_FORWARD = {
    Event.SUBMIT_STT: (State.UPLOADED, State.TRANSCRIBING),
    Event.STT_CALLBACK: (State.TRANSCRIBING, State.TRANSCRIBED),
    Event.START_ANALYSIS: (State.TRANSCRIBED, State.ANALYZING),
    Event.ANALYSIS_DONE: (State.ANALYZING, State.ANALYZED),
    Event.COMMIT: (State.ANALYZED, State.DONE),
}

# happy-path 선형 순서 — 멱등 판정(이미 지난 단계의 전진 이벤트 = no-op)에 쓴다.
# _FORWARD의 src→tgt 간선을 UPLOADED부터 이어 도출(단일 진실원) — 전이표만 고치면 순서도
# 자동 반영되어 이중 유지가 사라진다(순서 표를 빠뜨려 _ORDER[tgt]가 KeyError나는 함정 제거).
def _linear_order(forward):
    _next = {src: tgt for src, tgt in forward.values()}
    order, s = {}, State.UPLOADED
    while s is not None:
        order[s] = len(order)
        s = _next.get(s)
    return order


_ORDER = _linear_order(_FORWARD)

_TERMINAL = frozenset({State.DONE, State.PERMANENTLY_FAILED})

# 각 터미널 상태를 만든 이벤트 — 그 이벤트의 재전달(at-least-once)만 멱등 no-op으로 흡수한다.
# DONE←COMMIT(ANALYZED→DONE), PERMANENTLY_FAILED←RETRY(FAILED·예산 소진). 두 터미널을 대칭
# 처리(그 외 모든 이벤트는 fail-loud) — 소진 RETRY 재전달이 크래시-전-ack로 다시 와도 흡수.
_TERMINAL_MAKER = {
    State.DONE: Event.COMMIT,
    State.PERMANENTLY_FAILED: Event.RETRY,
}

# 실패 가능한(외부 작업) 스테이지 -> 재개 체크포인트(그 스테이지의 입력 상태).
# ANALYZING 실패는 TRANSCRIBED부터 재개(전사 이미 지불 → 재-STT 비용 회피).
_RESUME_POINT = {
    State.TRANSCRIBING: State.UPLOADED,
    State.ANALYZING: State.TRANSCRIBED,
}

# 상태 진입 불변식(단일 진실원): 이 상태의 잡은 이 ref 필드를 반드시 가진다. advance()가 모든
# 진입점(현재 상태)과 재개 결과에서 _check_state_invariant로 강제 — 손상 rehydrate가 빈 전사/결과
# 잡을 상태머신에 밀어넣어도 재-STT 회피 재개나 COMMIT이 빈 값 위에서 진행하지 못하게.
# (전진 이벤트는 추가로 '들어오는 인자'도 검사한다: STT_CALLBACK/ANALYSIS_DONE ref 강제. '전이로
#  쓰는 값'과 '잡의 현재 값'은 같은 불변식의 양면 — 후자가 직접 rehydrate 잡을 덮는다.)
_STATE_REQUIRES = {
    State.TRANSCRIBED: "transcript_ref",
    State.ANALYZED: "result_ref",
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
def _check_state_invariant(job: Job) -> None:
    """job이 현재 상태의 진입 불변식(_STATE_REQUIRES)을 만족하는지 확인 — 위반 시 fail-loud.
    손상 rehydrate가 빈 전사/결과 잡을 상태머신에 밀어넣는 걸 진입점·재개 양쪽에서 균일 차단.

    범위: 유료 재실행/빈 결과 커밋 등 '피해 나는' 경로를 막는 게 목적이라 None만 거부한다
    (전진 인자 검사와 동일 정책). 빈 문자열('')·계보(ANALYZING의 transcript 보존) 같은 전수
    무결성은 로드 경계를 소유한 PR2 rehydrate 검증에서 Job 단위로 마무리한다.
    """
    need = _STATE_REQUIRES.get(job.state)
    if need is not None and getattr(job, need) is None:
        raise IllegalTransition(
            f"{job.state.value} 상태 진입 불변식 위반: {need} 없음(빈 전사/결과 잡)")


def _required_refs(job: Job) -> tuple:
    """job의 '도달 단계'가 비어있지 않게 요구하는 ref 필드명들(계보 단일 출처).

    _STATE_REQUIRES(어느 상태가 어느 ref)와 _ORDER(진행 순서)에서 파생 — 계보를 이중 기재하지
    않는다. 도달 단계 이상의 모든 요구 ref를 누적하므로, _STATE_REQUIRES에 직접 없는 상태
    (ANALYZING/DONE)도 앞 단계 ref(transcript 등)를 물려받아 강제된다.
    - happy 상태: _ORDER[state]가 도달 단계.
    - FAILED: 실패 전 완료 체크포인트(_RESUME_POINT[failed_from])까지 도달 → 그 ref 보유해야.
    - PERMANENTLY_FAILED: 죽은 터미널(재개 없음) → 강제 없음(어느 단계서든 소진 가능)."""
    if job.state is State.PERMANENTLY_FAILED:
        return ()
    if job.state is State.FAILED:
        attained = _ORDER.get(_RESUME_POINT.get(job.failed_from), -1)
    else:
        attained = _ORDER.get(job.state, -1)
    return tuple(field for st, field in _STATE_REQUIRES.items()
                 if attained >= _ORDER[st])


def _is_blank_ref(value) -> bool:
    """ref가 '실 콘텐츠를 가리키지 못하는' 빈 값인가 — None·비-str·빈/공백 문자열 전부.

    str-only `== ""`로는 공백만("  ")·탭/개행·BLOB(b'')·비-str이 새어 계보 게이트를 관통한다
    (리뷰 CONFIRMED: sqlite TEXT affinity로 빈 BLOB이 b''로 rehydrate). isinstance+strip으로
    한 술어에 균일화 — '빈 전사 위 유료 재실행' 손상 클래스를 한 곳에서 막는다."""
    return not isinstance(value, str) or not value.strip()


def validate_persisted(job: Job) -> None:
    """로드/저장 경계 전수 무결성 — 필수 스칼라(id·audio_ref·attempts)·FAILED 정합·계보 ref.

    advance()의 _check_state_invariant는 전이 경로의 '피해 나는' None만 막는다(TRANSCRIBED/
    ANALYZED). 영속 경계는 그보다 강하게, '어느 스토어로도 왕복 가능한 잡인지'를 Job 단위로
    마감한다 — 손상 rehydrate(빈 전사·계보 끊긴 ANALYZING·재개 목표 없는 FAILED·필수 필드 누락)가
    상태머신이나 스토어에 진입하기 전에 CorruptJob. repository가 save/get 양쪽에서 호출(대칭 강제).

    필수 스칼라를 여기서 균일 거부해야 InMemory·Sqlite가 같은 입력에 같게 반응한다(포트 대체
    가능성). sqlite는 id/audio_ref/attempts에 NOT NULL을 걸어 raw IntegrityError로 크래시하지만
    InMemory엔 그 제약이 없어 조용히 수용한다 — 두 경로의 발산을 이 게이트가 막는다(리뷰 CONFIRMED).
    PR1 2차 리뷰의 '계약은 진입점 단일 출처에서 균일 강제' 교훈을 영속 경계에 적용."""
    # 모든 잡의 필수 스칼라 — 어느 상태서든 존재해야(sqlite NOT NULL 컬럼과 정합).
    if not isinstance(job.id, str) or not job.id.strip():
        raise CorruptJob(f"id가 비어있음(필수 식별자): {job.id!r}")
    if _is_blank_ref(job.audio_ref):
        raise CorruptJob(f"audio_ref가 비어있음(모든 잡의 필수 필드): {job.audio_ref!r}")
    if not isinstance(job.attempts, int) or job.attempts < 0:
        raise CorruptJob(f"attempts가 비음수 정수가 아님: {job.attempts!r}")
    # FAILED 재개 목표 정합 — 계보(_required_refs)가 well-defined하려면 먼저 확인.
    if job.state is State.FAILED and job.failed_from not in _RESUME_POINT:
        raise CorruptJob(
            f"FAILED인데 재개 목표(failed_from={job.failed_from})가 외부 작업 단계가 아님 — 계보 미정의")
    # 상태별 계보 ref — 도달 단계가 요구하는 ref는 실 콘텐츠를 가리키는 비어있지 않은 str이어야.
    for field in _required_refs(job):
        if _is_blank_ref(getattr(job, field)):
            raise CorruptJob(
                f"{job.state.value} 잡의 {field}가 비어있음(계보 위반: None/빈·공백/비-str)")


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

    # 0) 진입 불변식 — 지금 상태(from)가 요구하는 ref가 비면 어떤 이벤트든 fail-loud.
    #    손상 rehydrate(빈 전사/결과 잡)가 상태머신 어느 진입점으로도 못 들어오게 균일 방어.
    _check_state_invariant(job)

    # 1) 터미널: 그 상태를 만든 이벤트의 중복만 멱등 no-op, 나머지는 전부 거부.
    #    DONE←COMMIT, PERMANENTLY_FAILED←RETRY를 대칭 흡수(_TERMINAL_MAKER) — at-least-once
    #    재전달이 소진 RETRY라도 raise 대신 no-op(자기 자신을 만든 이벤트).
    if job.state in _TERMINAL:
        if _TERMINAL_MAKER.get(job.state) is event:
            return job                     # 예: DONE+COMMIT / PERMANENTLY_FAILED+RETRY(재전달)
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
            resumed = replace(job, state=resume, failed_from=None, error=None,
                              ts=clock.now())
            # 재개 결과의 진입 불변식 재확인 — 재-STT 회피 재개가 '빈 전사 위 재실행'으로 뚫지
            # 못하게(예: failed_from=ANALYZING인데 transcript_ref=None → TRANSCRIBED 재개 거부).
            _check_state_invariant(resumed)
            return resumed
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

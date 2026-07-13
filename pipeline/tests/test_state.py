"""상태머신 순수 코어 회귀 — 전이표·불변식·멱등·실패/재시도·비동기 형상·결정성 seam.

TDD: 엣지·에러 케이스(불법 전이·터미널 거부·멱등)를 happy path보다 먼저 건다.
이 코어는 크레덴셜 0·부작용 0 — 오케스트레이터(PR4)가 각 전이 뒤 영속(PR2)을 붙인다.
"""

import pytest

from pipeline_core.state import (
    Event,
    FixedClock,
    IllegalTransition,
    Job,
    SequentialIdSource,
    State,
    advance,
    new_job,
)

T0 = "2026-07-14T00:00:00+00:00"


def _job(state, **kw):
    """테스트용 Job 팩토리 — 명시한 필드만 덮어씀."""
    base = dict(
        id="job-1", state=state, audio_ref="s3://audio/1",
        transcript_ref=None, result_ref=None, failed_from=None,
        attempts=0, error=None, ts=T0,
    )
    base.update(kw)
    return Job(**base)


# ── 비동기 형상 못박기 (제출 ≠ 전사) ──────────────────────────────────
def test_submit_stays_in_transcribing_not_transcribed():
    j = advance(_job(State.UPLOADED), Event.SUBMIT_STT, clock=FixedClock(T0))
    assert j.state is State.TRANSCRIBING       # 제출됨 · 콜백 '대기'
    assert j.state is not State.TRANSCRIBED     # 동기 착각 금지(실 클로바=비동기)


def test_callback_before_submit_is_illegal():
    with pytest.raises(IllegalTransition):
        advance(_job(State.UPLOADED), Event.STT_CALLBACK, clock=FixedClock(T0))


def test_callback_moves_transcribing_to_transcribed_and_sets_ref():
    j = advance(_job(State.TRANSCRIBING), Event.STT_CALLBACK,
                clock=FixedClock(T0), transcript_ref="s3://t/1")
    assert j.state is State.TRANSCRIBED
    assert j.transcript_ref == "s3://t/1"


# ── 멱등 (중복 콜백/제출) ─────────────────────────────────────────────
def test_reapply_same_event_is_idempotent_noop():
    j0 = _job(State.TRANSCRIBED, transcript_ref="s3://t/1")
    j1 = advance(j0, Event.STT_CALLBACK, clock=FixedClock("t-late"),
                 transcript_ref="s3://t/DUP")
    assert j1.state is State.TRANSCRIBED
    assert j1.transcript_ref == "s3://t/1"      # 재적용은 덮어쓰지 않음
    assert j1 == j0                             # no-op: 원본 그대로


def test_late_callback_after_progress_is_idempotent_noop():
    j0 = _job(State.ANALYZED, transcript_ref="s3://t/1", result_ref="r/1")
    j1 = advance(j0, Event.STT_CALLBACK, clock=FixedClock("t"), transcript_ref="x")
    assert j1 == j0                             # 이미 지나간 단계 → no-op(퇴행 금지)


# ── happy path 완주 ───────────────────────────────────────────────────
def test_happy_path_full_traversal():
    clk = FixedClock(T0)
    j = new_job("s3://audio/1", clock=clk, ids=SequentialIdSource())
    assert j.state is State.UPLOADED
    j = advance(j, Event.SUBMIT_STT, clock=clk)
    j = advance(j, Event.STT_CALLBACK, clock=clk, transcript_ref="t/1")
    j = advance(j, Event.START_ANALYSIS, clock=clk)
    j = advance(j, Event.ANALYSIS_DONE, clock=clk, result_ref="r/1")
    assert j.state is State.ANALYZED            # 결과 산출됨 · 커밋 전(정확히-한번 seam)
    j = advance(j, Event.COMMIT, clock=clk)
    assert j.state is State.DONE
    assert j.transcript_ref == "t/1" and j.result_ref == "r/1"


def test_skipping_a_stage_is_illegal():
    with pytest.raises(IllegalTransition):
        advance(_job(State.UPLOADED), Event.START_ANALYSIS, clock=FixedClock(T0))


# ── 실패 / 재시도 / 재개 ──────────────────────────────────────────────
def test_fail_records_failed_from_attempts_and_error():
    j = advance(_job(State.ANALYZING), Event.FAIL, clock=FixedClock(T0),
                error="detector 500")
    assert j.state is State.FAILED
    assert j.failed_from is State.ANALYZING
    assert j.attempts == 1
    assert j.error == "detector 500"


def test_analyzing_failure_retry_resumes_at_transcribed_not_stt():
    failed = _job(State.FAILED, failed_from=State.ANALYZING, attempts=1,
                  error="x", transcript_ref="t/1")
    j = advance(failed, Event.RETRY, clock=FixedClock(T0))
    assert j.state is State.TRANSCRIBED         # 전사 이미 지불 → 재-STT 비용 회피
    assert j.state is not State.TRANSCRIBING
    assert j.failed_from is None and j.error is None
    assert j.attempts == 1                      # 누적 유지
    assert j.transcript_ref == "t/1"


def test_transcribing_failure_retry_resumes_at_uploaded():
    failed = _job(State.FAILED, failed_from=State.TRANSCRIBING, attempts=1, error="x")
    j = advance(failed, Event.RETRY, clock=FixedClock(T0))
    assert j.state is State.UPLOADED            # STT부터 다시(제출 전으로)


def test_max_attempts_exhausted_goes_permanently_failed():
    failed = _job(State.FAILED, failed_from=State.ANALYZING, attempts=3, error="x")
    j = advance(failed, Event.RETRY, clock=FixedClock(T0), max_attempts=3)
    assert j.state is State.PERMANENTLY_FAILED
    assert j.failed_from is State.ANALYZING     # 진단 위해 보존
    assert j.error == "x"


def test_fail_from_non_active_stage_is_illegal():
    for s in (State.UPLOADED, State.TRANSCRIBED, State.ANALYZED):
        with pytest.raises(IllegalTransition):
            advance(_job(s), Event.FAIL, clock=FixedClock(T0), error="x")


def test_retry_from_non_failed_is_illegal():
    with pytest.raises(IllegalTransition):
        advance(_job(State.ANALYZING), Event.RETRY, clock=FixedClock(T0))


# ── 터미널 상태 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("ev", list(Event))
def test_permanently_failed_rejects_all_events(ev):
    with pytest.raises(IllegalTransition):
        advance(_job(State.PERMANENTLY_FAILED, failed_from=State.ANALYZING),
                ev, clock=FixedClock(T0), error="x", transcript_ref="t", result_ref="r")


def test_done_noops_duplicate_commit():
    # at-least-once 재전달 흡수 — 터미널을 만든 이벤트(COMMIT)의 중복은 멱등 no-op.
    done = _job(State.DONE, transcript_ref="t/1", result_ref="r/1")
    j = advance(done, Event.COMMIT, clock=FixedClock("later"))
    assert j == done


@pytest.mark.parametrize("ev", [
    Event.FAIL, Event.RETRY, Event.SUBMIT_STT,
    Event.STT_CALLBACK, Event.START_ANALYSIS, Event.ANALYSIS_DONE,
])
def test_done_rejects_non_commit_events(ev):
    with pytest.raises(IllegalTransition):
        advance(_job(State.DONE, transcript_ref="t/1", result_ref="r/1"),
                ev, clock=FixedClock(T0), error="x", transcript_ref="t", result_ref="r")


# ── 추가 회귀 (적대적 리뷰 갭 닫기) ────────────────────────────────────
def test_duplicate_submit_stt_is_idempotent_noop():
    j0 = _job(State.TRANSCRIBING)
    j1 = advance(j0, Event.SUBMIT_STT, clock=FixedClock("later"))
    assert j1 == j0                             # 중복 제출 → no-op(재-STT/유료 중복 차단)


def test_callback_without_transcript_ref_is_illegal():
    # 전사 없는 전진 금지 — "TRANSCRIBED=전사 있음" 불변식(재-STT 회피 재개가 이걸 전제).
    with pytest.raises(IllegalTransition):
        advance(_job(State.TRANSCRIBING), Event.STT_CALLBACK, clock=FixedClock(T0))


def test_analysis_done_without_result_ref_is_illegal():
    with pytest.raises(IllegalTransition):
        advance(_job(State.ANALYZING), Event.ANALYSIS_DONE, clock=FixedClock(T0))


def test_forward_event_on_failed_is_illegal():
    with pytest.raises(IllegalTransition):
        advance(_job(State.FAILED, failed_from=State.ANALYZING, attempts=1),
                Event.START_ANALYSIS, clock=FixedClock(T0))


def test_retry_budget_boundary_resumes_when_attempts_below_max():
    failed = _job(State.FAILED, failed_from=State.ANALYZING, attempts=2,
                  error="x", transcript_ref="t/1")
    j = advance(failed, Event.RETRY, clock=FixedClock(T0), max_attempts=3)
    assert j.state is State.TRANSCRIBED         # 2 < 3 → 재개


def test_transcribing_failure_retry_clears_failed_from_and_error():
    failed = _job(State.FAILED, failed_from=State.TRANSCRIBING, attempts=1, error="x")
    j = advance(failed, Event.RETRY, clock=FixedClock(T0))
    assert j.state is State.UPLOADED
    assert j.failed_from is None and j.error is None


def test_multi_cycle_retry_until_permanently_failed():
    """FAIL→RETRY 다주기를 실제로 돌려 attempts 회계·무한루프 부재를 고정."""
    clk = FixedClock(T0)
    j = _job(State.TRANSCRIBED, transcript_ref="t/1")
    seen = []
    for _ in range(6):                          # 상한(3) 초과해도 안전
        j = advance(j, Event.START_ANALYSIS, clock=clk)
        assert j.state is State.ANALYZING
        j = advance(j, Event.FAIL, clock=clk, error="boom")
        assert j.state is State.FAILED
        seen.append(j.attempts)
        j = advance(j, Event.RETRY, clock=clk, max_attempts=3)
        if j.state is State.PERMANENTLY_FAILED:
            break
        assert j.state is State.TRANSCRIBED     # 재개 = 재-STT 회피(전사 보존)
        assert j.transcript_ref == "t/1"
    assert j.state is State.PERMANENTLY_FAILED
    assert seen == [1, 2, 3]                     # 무한증가/무한루프 없음


def test_job_coerces_string_state_and_failed_from_to_enum():
    # PR2가 sqlite에서 문자열로 rehydrate해도 identity(`is`) 재개 경로가 성립해야 한다.
    j = Job(id="x", state="failed", audio_ref="a", failed_from="analyzing",
            attempts=1, error="e", ts=T0)
    assert j.state is State.FAILED
    assert j.failed_from is State.ANALYZING
    r = advance(j, Event.RETRY, clock=FixedClock(T0))
    assert r.state is State.TRANSCRIBED         # 문자열이었어도 RETRY 정상


def test_job_rejects_unknown_state_string():
    with pytest.raises(ValueError):
        Job(id="x", state="bogus", audio_ref="a", ts=T0)


# ── 결정성 seam (주입 Clock / IdSource) ───────────────────────────────
def test_new_job_uses_injected_clock_and_idsource():
    j = new_job("s3://a/1", clock=FixedClock("T-FIXED"), ids=SequentialIdSource("m"))
    assert j.id == "m-1"
    assert j.ts == "T-FIXED"
    assert j.state is State.UPLOADED
    assert j.attempts == 0


def test_transition_updates_ts_from_clock():
    j = advance(_job(State.UPLOADED, ts="old"), Event.SUBMIT_STT,
                clock=FixedClock("T-NEW"))
    assert j.ts == "T-NEW"


def test_sequential_idsource_increments_deterministically():
    ids = SequentialIdSource()
    assert [ids.new_id(), ids.new_id(), ids.new_id()] == ["job-1", "job-2", "job-3"]


def test_job_is_frozen_immutable():
    j = _job(State.UPLOADED)
    with pytest.raises(Exception):              # frozen dataclass → FrozenInstanceError
        j.state = State.DONE

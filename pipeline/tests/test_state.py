"""상태머신 순수 코어 회귀 — 전이표·불변식·멱등·실패/재시도·비동기 형상·결정성 seam.

TDD: 엣지·에러 케이스(불법 전이·터미널 거부·멱등)를 happy path보다 먼저 건다.
이 코어는 크레덴셜 0·부작용 0 — 오케스트레이터(PR4)가 각 전이 뒤 영속(PR2)을 붙인다.
"""

from dataclasses import FrozenInstanceError

import pytest

from pipeline_core.state import (
    CorruptJob,
    Event,
    FixedClock,
    IllegalTransition,
    Job,
    SequentialIdSource,
    State,
    advance,
    new_job,
    validate_persisted,
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
    # 유효 ref를 줘 진입 불변식은 충족 → FAIL이 '외부 작업 단계 아님'으로 거부되는지를 순수 검증.
    for j in (_job(State.UPLOADED),
              _job(State.TRANSCRIBED, transcript_ref="t/1"),
              _job(State.ANALYZED, transcript_ref="t/1", result_ref="r/1")):
        with pytest.raises(IllegalTransition):
            advance(j, Event.FAIL, clock=FixedClock(T0), error="x")


def test_retry_from_non_failed_is_illegal():
    with pytest.raises(IllegalTransition):
        advance(_job(State.ANALYZING), Event.RETRY, clock=FixedClock(T0))


# ── 터미널 상태 ───────────────────────────────────────────────────────
# PERMANENTLY_FAILED를 만든 이벤트는 RETRY(예산 소진). 그 RETRY의 재전달만 멱등 no-op이고,
# 나머지 이벤트는 전부 거부 — DONE(만든 이벤트=COMMIT)과 대칭.
@pytest.mark.parametrize("ev", [e for e in Event if e is not Event.RETRY])
def test_permanently_failed_rejects_non_maker_events(ev):
    with pytest.raises(IllegalTransition):
        advance(_job(State.PERMANENTLY_FAILED, failed_from=State.ANALYZING),
                ev, clock=FixedClock(T0), error="x", transcript_ref="t", result_ref="r")


def test_permanently_failed_noops_duplicate_retry():
    # at-least-once: PERMANENTLY_FAILED를 만든 그 RETRY의 재전달(크래시-전-ack 재전송)은
    # 멱등 no-op이어야 한다 — DONE+재-COMMIT과 대칭(docstring 규칙 b의 균일 적용).
    pf = _job(State.PERMANENTLY_FAILED, failed_from=State.ANALYZING, attempts=3)
    j = advance(pf, Event.RETRY, clock=FixedClock("later"), max_attempts=3)
    assert j == pf                              # 재전달 흡수 — raise 아님


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


def test_duplicate_analysis_done_on_analyzed_is_idempotent_noop():
    # 유료 분석 스테이지의 재전달 흡수 — 이미 ANALYZED면 중복 ANALYSIS_DONE는 no-op.
    # (STT_CALLBACK만 커버돼 있던 갭: 커밋된 result_ref를 stale 값으로 덮어쓰지 않음을 고정.)
    j0 = _job(State.ANALYZED, transcript_ref="t/1", result_ref="r/ORIG")
    j1 = advance(j0, Event.ANALYSIS_DONE, clock=FixedClock("later"), result_ref="r/DUP")
    assert j1 == j0
    assert j1.result_ref == "r/ORIG"            # 재적용은 덮어쓰지 않음


# ── 상태 진입 불변식 (손상 rehydrate 균일 방어) ───────────────────────
# 전진 인자 검사만으론 '직접 rehydrate된 빈 전사/결과 잡'을 못 막는다. advance()가 진입점과
# 재개 결과에서 상태 불변식(_STATE_REQUIRES: TRANSCRIBED=전사·ANALYZED=결과)을 재확인해야 한다.
def test_commit_on_analyzed_missing_result_ref_is_illegal():
    # result_ref 없는 ANALYZED를 COMMIT하면 '빈 결과'가 DONE(정확히-한번 seam)으로 커밋됨 → fail-loud.
    corrupt = _job(State.ANALYZED, transcript_ref="t/1", result_ref=None)
    with pytest.raises(IllegalTransition):
        advance(corrupt, Event.COMMIT, clock=FixedClock(T0))


def test_forward_from_transcribed_missing_transcript_ref_is_illegal():
    # transcript_ref 없는 TRANSCRIBED에서 START_ANALYSIS로 전진하면 '빈 전사 위 분석' → fail-loud.
    corrupt = _job(State.TRANSCRIBED, transcript_ref=None)
    with pytest.raises(IllegalTransition):
        advance(corrupt, Event.START_ANALYSIS, clock=FixedClock(T0))


def test_any_event_on_corrupt_transcribed_is_illegal():
    # 이벤트 종류 무관 — 빈 전사 TRANSCRIBED 잡은 어느 이벤트로도 전진 불가(진입점 균일 방어).
    corrupt = _job(State.TRANSCRIBED, transcript_ref=None)
    for ev in (Event.STT_CALLBACK, Event.START_ANALYSIS, Event.COMMIT):
        with pytest.raises(IllegalTransition):
            advance(corrupt, ev, clock=FixedClock(T0),
                    transcript_ref="t", result_ref="r")


def test_valid_states_pass_invariant_guard_no_false_positive():
    # 무결 잡은 통과 — 가드가 정상 흐름을 오탐하지 않음(회귀 방지).
    ok_t = _job(State.TRANSCRIBED, transcript_ref="t/1")
    assert advance(ok_t, Event.START_ANALYSIS, clock=FixedClock(T0)).state is State.ANALYZING
    ok_a = _job(State.ANALYZED, transcript_ref="t/1", result_ref="r/1")
    assert advance(ok_a, Event.COMMIT, clock=FixedClock(T0)).state is State.DONE


def test_forward_event_on_failed_is_illegal():
    with pytest.raises(IllegalTransition):
        advance(_job(State.FAILED, failed_from=State.ANALYZING, attempts=1),
                Event.START_ANALYSIS, clock=FixedClock(T0))


def test_retry_budget_boundary_resumes_when_attempts_below_max():
    failed = _job(State.FAILED, failed_from=State.ANALYZING, attempts=2,
                  error="x", transcript_ref="t/1")
    j = advance(failed, Event.RETRY, clock=FixedClock(T0), max_attempts=3)
    assert j.state is State.TRANSCRIBED         # 2 < 3 → 재개


def test_retry_resume_requires_transcript_ref_fail_loud():
    # 손상 rehydrate(failed_from=ANALYZING인데 transcript_ref 없음): TRANSCRIBED로 재개하면
    # '빈 전사 위 재감지'가 되므로 fail-loud. 전진 STT_CALLBACK 경로와 같은 불변식을 재개에도 강제.
    corrupt = _job(State.FAILED, failed_from=State.ANALYZING, attempts=1,
                   error="x", transcript_ref=None)
    with pytest.raises(IllegalTransition):
        advance(corrupt, Event.RETRY, clock=FixedClock(T0))


def test_retry_resume_to_uploaded_needs_no_transcript_ref():
    # 대조: TRANSCRIBING 실패는 UPLOADED로 재개 — UPLOADED 진입 불변식엔 transcript_ref 불요.
    failed = _job(State.FAILED, failed_from=State.TRANSCRIBING, attempts=1,
                  error="x", transcript_ref=None)
    j = advance(failed, Event.RETRY, clock=FixedClock(T0))
    assert j.state is State.UPLOADED            # 손상 아님 — 정상 재개


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
    # transcript_ref는 유효한 잡의 전제(ANALYZING까지 갔으면 전사 존재) — 재개 불변식 충족.
    j = Job(id="x", state="failed", audio_ref="a", failed_from="analyzing",
            attempts=1, error="e", ts=T0, transcript_ref="t/1")
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


def test_fail_updates_ts_from_clock():
    # FAIL 분기도 ts를 주입 Clock에서 갱신 — PR5 백오프/타임아웃이 이 필드에 의존.
    j = advance(_job(State.ANALYZING, ts="old"), Event.FAIL,
                clock=FixedClock("T-FAIL"), error="x")
    assert j.ts == "T-FAIL"


def test_retry_resume_updates_ts_from_clock():
    failed = _job(State.FAILED, failed_from=State.ANALYZING, attempts=1,
                  error="x", transcript_ref="t/1", ts="old")
    j = advance(failed, Event.RETRY, clock=FixedClock("T-RETRY"))
    assert j.state is State.TRANSCRIBED and j.ts == "T-RETRY"


def test_retry_exhausted_updates_ts_from_clock():
    failed = _job(State.FAILED, failed_from=State.ANALYZING, attempts=3, ts="old")
    j = advance(failed, Event.RETRY, clock=FixedClock("T-PF"), max_attempts=3)
    assert j.state is State.PERMANENTLY_FAILED and j.ts == "T-PF"


def test_sequential_idsource_increments_deterministically():
    ids = SequentialIdSource()
    assert [ids.new_id(), ids.new_id(), ids.new_id()] == ["job-1", "job-2", "job-3"]


def test_job_is_frozen_immutable():
    j = _job(State.UPLOADED)
    with pytest.raises(FrozenInstanceError):     # 구체 예외 — 무관한 에러로 통과 방지
        j.state = State.DONE
    assert j.state is State.UPLOADED             # 변경 시도가 필드를 건드리지 않았음


# ── 로드 경계 전수 무결성 (validate_persisted — PR2 rehydrate가 호출) ──────
# advance()의 _check_state_invariant는 '피해 경로'만 None-거부(TRANSCRIBED/ANALYZED). 로드/저장
# 경계는 더 강하게 Job 단위로 마감한다: 빈 문자열('') ref·상태별 전 ref 계보(ANALYZING/DONE도
# transcript 보존)·FAILED 재개 목표 정합. 손상 rehydrate가 상태머신에 진입하기 전에 fail-loud.
# 계보 요구는 _STATE_REQUIRES+_ORDER에서 파생(단일 출처) — advance 표를 고치면 여기도 자동 반영.
def test_validate_persisted_accepts_valid_snapshots():
    for j in (
        _job(State.UPLOADED),
        _job(State.TRANSCRIBING),
        _job(State.TRANSCRIBED, transcript_ref="t/1"),
        _job(State.ANALYZING, transcript_ref="t/1"),
        _job(State.ANALYZED, transcript_ref="t/1", result_ref="r/1"),
        _job(State.DONE, transcript_ref="t/1", result_ref="r/1"),
        _job(State.FAILED, failed_from=State.TRANSCRIBING, attempts=1, error="x"),
        _job(State.FAILED, failed_from=State.ANALYZING, attempts=1, error="x",
             transcript_ref="t/1"),
        _job(State.PERMANENTLY_FAILED, failed_from=State.ANALYZING, attempts=3),
    ):
        validate_persisted(j)                    # 무결 잡은 예외 없이 통과(오탐 방지)


def test_validate_persisted_rejects_empty_string_transcript_ref():
    # 잔여 결함 (a): ''는 _check_state_invariant(None-only)를 통과하지만 '빈 전사'라 재감지 위험.
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.TRANSCRIBED, transcript_ref=""))


def test_validate_persisted_rejects_analyzing_without_transcript():
    # 잔여 결함 (b) 계보: ANALYZING은 _STATE_REQUIRES에 없어 None이 통과하지만, TRANSCRIBED에서
    # 온 계보상 transcript_ref가 반드시 있어야 한다(없으면 ANALYSIS_DONE이 빈 전사 위 결과 커밋).
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.ANALYZING, transcript_ref=None))


def test_validate_persisted_rejects_analyzing_empty_transcript():
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.ANALYZING, transcript_ref=""))


def test_validate_persisted_rejects_done_missing_result():
    # 계보: DONE은 transcript+result 둘 다 보존해야 한다(_STATE_REQUIRES가 DONE을 안 담아도).
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.DONE, transcript_ref="t/1", result_ref=None))


def test_validate_persisted_rejects_analyzed_empty_result():
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.ANALYZED, transcript_ref="t/1", result_ref=""))


def test_validate_persisted_rejects_failed_analyzing_without_transcript():
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.FAILED, failed_from=State.ANALYZING,
                                attempts=1, error="x", transcript_ref=None))


def test_validate_persisted_accepts_failed_transcribing_without_transcript():
    # 대조: TRANSCRIBING 실패는 아직 전사 없음 — 계보상 transcript_ref 불요(정상).
    validate_persisted(_job(State.FAILED, failed_from=State.TRANSCRIBING,
                            attempts=1, error="x", transcript_ref=None))


def test_validate_persisted_rejects_failed_without_valid_failed_from():
    # FAILED인데 재개 목표(failed_from)가 없거나 외부작업 단계가 아니면 계보 미정의 → 손상.
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.FAILED, failed_from=None, attempts=1, error="x"))
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.FAILED, failed_from=State.UPLOADED,
                                attempts=1, error="x"))


def test_validate_persisted_permanently_failed_lenient_on_refs():
    # 죽은 터미널(재개 없음) — 어느 단계서든 소진 가능하므로 ref 계보 강제 안 함.
    validate_persisted(_job(State.PERMANENTLY_FAILED, failed_from=State.TRANSCRIBING,
                            attempts=3, transcript_ref=None, result_ref=None))


# ── 필수 스칼라·강한 빈값 술어 (리뷰 반영: 포트 대체 가능성·계보 게이트 강화) ──
# validate_persisted가 계보 ref만 보면, sqlite NOT NULL(id/audio_ref/attempts)은 raw
# IntegrityError로 터지지만 InMemory는 조용히 수용 → 두 스토어 발산. 필수 스칼라를 게이트에서
# 균일 거부해 대체 가능성을 회복한다. 또 str-only `== ""`는 공백만·비-str을 놓치므로 강화한다.
def test_validate_persisted_rejects_missing_audio_ref():
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.UPLOADED, audio_ref=None))


def test_validate_persisted_rejects_blank_audio_ref():
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.UPLOADED, audio_ref="   "))


def test_validate_persisted_rejects_empty_id():
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.UPLOADED, id=""))


def test_validate_persisted_rejects_bad_attempts():
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.UPLOADED, attempts=None))     # sqlite NOT NULL 대칭
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.UPLOADED, attempts=-1))       # 비음수 위반


def test_validate_persisted_rejects_whitespace_only_ref():
    # 빈 문자열뿐 아니라 공백만/탭·개행만 ref도 '빈 전사'라 거부(str-only == "" 가 놓쳤던 클래스).
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.TRANSCRIBED, transcript_ref="   "))
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.ANALYZED, transcript_ref="t/1", result_ref="\t\n"))


def test_validate_persisted_rejects_non_str_ref():
    # 비-str ref(bytes 등) — sqlite TEXT affinity로 BLOB(b'')이 rehydrate되는 경로 방어.
    with pytest.raises(CorruptJob):
        validate_persisted(_job(State.TRANSCRIBED, transcript_ref=b""))

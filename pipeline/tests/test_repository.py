"""JobRepository 영속화 회귀 — 재시작 복원·낙관적 동시성·로드 경계 무결성·UTF-8·팩토리.

TDD: 손상 로우 거부·stale 버전 거부(엣지)를 happy 저장/복원보다 먼저 건다. sqlite는 실제 tmp
파일로 '새 인스턴스가 같은 파일을 다시 열어 복원'을 검증한다(:memory:는 커넥션별이라 부적합).
계약 테스트는 InMemory·Sqlite 두 구현에 같은 assert를 건다(포트 대체 가능성 고정).
"""

import sqlite3

import pytest

from pipeline_core.repository import (
    InMemoryJobRepository,
    SqliteJobRepository,
    StaleVersionError,
    get_store,
)
from pipeline_core.state import (
    CorruptJob,
    Event,
    FixedClock,
    Job,
    SequentialIdSource,
    State,
    advance,
    new_job,
)

T0 = "2026-07-14T00:00:00+00:00"


def _job(state, **kw):
    """테스트용 Job 팩토리 — 명시한 필드만 덮어씀(test_state._job과 동일 계약)."""
    base = dict(
        id="job-1", state=state, audio_ref="s3://audio/1",
        transcript_ref=None, result_ref=None, failed_from=None,
        attempts=0, error=None, ts=T0,
    )
    base.update(kw)
    return Job(**base)


@pytest.fixture(params=["memory", "sqlite"])
def repo(request, tmp_path):
    """두 구현을 같은 계약으로 검증하는 파라미터화 픽스처."""
    if request.param == "sqlite":
        return SqliteJobRepository(str(tmp_path / "jobs.db"))
    return InMemoryJobRepository()


# ── 저장/복원 왕복 (두 구현 동일 계약) ────────────────────────────────
def test_save_and_get_roundtrip(repo):
    job = _job(State.ANALYZING, transcript_ref="t/1", attempts=1)
    repo.save(job)
    assert repo.get("job-1") == job              # frozen dataclass 완전 동치


def test_get_absent_returns_none(repo):
    assert repo.get("nope") is None


# ── 낙관적 동시성 (버전 회계) ─────────────────────────────────────────
def test_version_of_absent_is_zero(repo):
    assert repo.version_of("nope") == 0


def test_first_save_returns_version_one(repo):
    assert repo.save(_job(State.UPLOADED)) == 1
    assert repo.version_of("job-1") == 1


def test_version_increments_on_each_save(repo):
    repo.save(_job(State.UPLOADED))                              # v1
    assert repo.save(_job(State.TRANSCRIBING), expected_version=1) == 2
    assert repo.version_of("job-1") == 2


def test_first_save_with_nonzero_expected_version_rejected(repo):
    with pytest.raises(StaleVersionError):
        repo.save(_job(State.UPLOADED), expected_version=5)


def test_stale_version_write_rejected(repo):
    repo.save(_job(State.UPLOADED))                             # v1
    repo.save(_job(State.TRANSCRIBING), expected_version=1)     # v2 (다른 라이터가 먼저 갱신)
    with pytest.raises(StaleVersionError):                      # 오래된 v1 읽기로 저장 시도
        repo.save(_job(State.TRANSCRIBED, transcript_ref="t/1"), expected_version=1)


# ── 열거 (재시작 복원 시 in-flight 잡 회수용 프리미티브) ────────────────
def test_all_jobs_enumerates_saved(repo):
    repo.save(_job(State.UPLOADED, id="a"))
    repo.save(_job(State.TRANSCRIBED, id="b", transcript_ref="t/1"))
    by_id = {j.id: j for j in repo.all_jobs()}
    assert set(by_id) == {"a", "b"}
    assert by_id["b"].transcript_ref == "t/1"


# ── 저장 경계 fail-loud (로드와 대칭) ──────────────────────────────────
def test_save_rejects_corrupt_job(repo):
    # 앱 버그가 손상 잡(빈 전사 위 ANALYZING)을 영속화하지 못하게 — 저장에서도 계보 검증.
    with pytest.raises(CorruptJob):
        repo.save(_job(State.ANALYZING, transcript_ref=None))


# ── sqlite 재시작 복원 (새 인스턴스가 같은 파일 재오픈) ────────────────
def test_sqlite_reload_restores_state(tmp_path):
    path = str(tmp_path / "jobs.db")
    job = _job(State.ANALYZING, transcript_ref="t/1", attempts=1)
    SqliteJobRepository(path).save(job)
    # 프로세스 재시작 모사 — 완전히 새 인스턴스가 같은 파일을 다시 연다.
    restored = SqliteJobRepository(path).get("job-1")
    assert restored == job
    assert restored.state is State.ANALYZING     # 문자열→enum 정규화까지 성립


def test_sqlite_all_jobs_survives_reload(tmp_path):
    path = str(tmp_path / "many.db")
    r = SqliteJobRepository(path)
    r.save(_job(State.UPLOADED, id="a"))
    r.save(_job(State.ANALYZED, id="b", transcript_ref="t/1", result_ref="r/1"))
    ids = {j.id for j in SqliteJobRepository(path).all_jobs()}
    assert ids == {"a", "b"}


def test_sqlite_utf8_korean_roundtrip(tmp_path):
    # 한글 error/transcript_ref가 CP949 깨짐 없이 왕복(sqlite TEXT=UTF-8)을 못박음.
    path = str(tmp_path / "ko.db")
    job = _job(State.FAILED, failed_from=State.ANALYZING, attempts=1,
               transcript_ref="전사/3월-회의.json",
               error="분석 실패: 결제 모듈 응답 없음")
    SqliteJobRepository(path).save(job)
    restored = SqliteJobRepository(path).get("job-1")
    assert restored == job
    assert restored.error == "분석 실패: 결제 모듈 응답 없음"      # 모지바케 아님
    assert restored.transcript_ref == "전사/3월-회의.json"


# ── sqlite 로드 경계: 손상 로우 거부 (저장 우회 = 스토리지 손상/외부 라이터 모사) ──
def _raw_insert(path, **overrides):
    """스키마에 로우를 직접 주입 — save의 검증 게이트를 우회해 손상 스토리지를 모사한다."""
    cols = {"id": "job-1", "state": "uploaded", "audio_ref": "a",
            "transcript_ref": None, "result_ref": None, "failed_from": None,
            "attempts": 0, "error": None, "ts": T0, "version": 1}
    cols.update(overrides)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO jobs (id, state, audio_ref, transcript_ref, result_ref, "
            "failed_from, attempts, error, ts, version) VALUES "
            "(:id, :state, :audio_ref, :transcript_ref, :result_ref, "
            ":failed_from, :attempts, :error, :ts, :version)", cols)
        conn.commit()
    finally:
        conn.close()


def test_sqlite_get_rejects_empty_ref_row(tmp_path):
    path = str(tmp_path / "c1.db")
    SqliteJobRepository(path)                     # 스키마 생성(커밋)
    _raw_insert(path, state="transcribed", transcript_ref="")    # '' 빈 전사
    with pytest.raises(CorruptJob):
        SqliteJobRepository(path).get("job-1")


def test_sqlite_get_rejects_analyzing_without_transcript_row(tmp_path):
    path = str(tmp_path / "c2.db")
    SqliteJobRepository(path)
    _raw_insert(path, state="analyzing", transcript_ref=None)    # 계보 위반
    with pytest.raises(CorruptJob):
        SqliteJobRepository(path).get("job-1")


def test_sqlite_get_rejects_unknown_state_row(tmp_path):
    path = str(tmp_path / "c3.db")
    SqliteJobRepository(path)
    _raw_insert(path, state="bogus")              # 알 수 없는 상태 문자열
    with pytest.raises(CorruptJob):
        SqliteJobRepository(path).get("job-1")


def test_sqlite_get_rejects_whitespace_ref_row(tmp_path):
    # 공백만 ref도 로드 경계에서 거부(str-only == "" 강화) — '빈 전사 위 재실행' 관통 차단.
    path = str(tmp_path / "c4.db")
    SqliteJobRepository(path)
    _raw_insert(path, state="transcribed", transcript_ref="   ")
    with pytest.raises(CorruptJob):
        SqliteJobRepository(path).get("job-1")


# ── 리뷰 반영: 포트 대체 가능성(필수 스칼라 균일 거부) + :memory: 비호환 가드 ──
def test_save_rejects_missing_audio_ref(repo):
    # audio_ref=None은 두 스토어 모두 CorruptJob이어야(Sqlite raw IntegrityError 아님) —
    # validate_persisted가 필수 스칼라를 균일 거부해 InMemory·Sqlite 발산을 막는다.
    with pytest.raises(CorruptJob):
        repo.save(_job(State.UPLOADED, audio_ref=None))


def test_get_store_rejects_memory_path():
    # :memory:는 연산별 커넥션 설계와 근본 비호환(각 커넥션이 새 빈 DB) — InMemory로 안내.
    with pytest.raises(ValueError):
        get_store("sqlite", path=":memory:")


# ── 팩토리 (detect.get_detector 미러링) ───────────────────────────────
def test_get_store_returns_memory():
    assert isinstance(get_store("memory"), InMemoryJobRepository)


def test_get_store_returns_sqlite(tmp_path):
    assert isinstance(get_store("sqlite", path=str(tmp_path / "j.db")),
                      SqliteJobRepository)


def test_get_store_sqlite_requires_path():
    with pytest.raises(ValueError):
        get_store("sqlite")


def test_get_store_unknown_raises():
    with pytest.raises(ValueError):
        get_store("bogus")


# ── 통합: 상태머신 전이 + 영속을 재시작 넘어 완주 (PR4 워킹스켈레톤 씨앗) ──
def test_persist_advance_sequence_across_restart(tmp_path):
    path = str(tmp_path / "flow.db")
    clk = FixedClock(T0)
    repo = SqliteJobRepository(path)
    job = new_job("s3://audio/1", clock=clk, ids=SequentialIdSource())
    jid = job.id
    v = repo.save(job)                                        # UPLOADED v1
    job = advance(job, Event.SUBMIT_STT, clock=clk)
    v = repo.save(job, expected_version=v)                    # TRANSCRIBING v2
    job = advance(job, Event.STT_CALLBACK, clock=clk, transcript_ref="t/1")
    v = repo.save(job, expected_version=v)                    # TRANSCRIBED v3

    # ── 프로세스 재시작 모사 — 새 인스턴스가 파일에서 복원해 이어감 ──
    repo2 = SqliteJobRepository(path)
    job = repo2.get(jid)
    assert job.state is State.TRANSCRIBED and job.transcript_ref == "t/1"
    v = repo2.version_of(jid)
    assert v == 3
    job = advance(job, Event.START_ANALYSIS, clock=clk)
    v = repo2.save(job, expected_version=v)
    job = advance(job, Event.ANALYSIS_DONE, clock=clk, result_ref="r/1")
    v = repo2.save(job, expected_version=v)
    job = advance(job, Event.COMMIT, clock=clk)
    repo2.save(job, expected_version=v)
    assert repo2.get(jid).state is State.DONE

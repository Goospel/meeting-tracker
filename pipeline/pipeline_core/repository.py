"""잡 영속화 — 상태머신 스냅샷을 저장/복원하는 포트(InMemory fake + stdlib sqlite3).

## 역할 (오케스트레이터 바깥의 영속 경계)
순수 코어(state.py)는 IO 0이다. 오케스트레이터(PR4)가 각 전이 뒤 여기에 저장하고, 재시작 시
복원한다. 런타임 의존성 0 DNA 계승 — sqlite3는 표준 라이브러리라 서드파티 유입 0.

## 낙관적 동시성 (버전 컬럼) — stage 5 선하드닝
save(job, expected_version=N)은 저장본 버전이 정확히 N일 때만 성공하고 N+1을 반환한다. 버전
불일치(오래된 읽기·버전 이동)는 StaleVersionError. 단일사용자 MVP엔 동시 라이터가 없어, 이
검사가 실제로 잡는 건 '오래된 버전으로 덮어쓰기' 논리 오류다.
⚠️ 범위(리뷰 반영 — 과장 금지): 진짜 동시 라이터가 있는 stage 5에서만 나타나는 두 경로 —
(a) 같은 id 동시 최초저장(INSERT의 UNIQUE 경합) (b) 쓰기락 경합(busy_timeout·재시도) — 은 이
PR이 다루지 않는다(투기적 멀티워커 코드는 YAGNI로 미도입, RETRYING 뺀 것과 동일 판단). 지금은
버전 컬럼·검사 seam만 박아, 그때 이 둘을 StaleVersionError로 흡수하며 재작성 없이 잇는다.

## 로드/저장 경계 무결성 (계보 전수 검증)
state.validate_persisted를 save·get 양쪽에서 호출한다(대칭). PR1 _check_state_invariant는
전이 경로의 '피해 나는' None만 막았고, 빈 문자열('') ref·상태별 전 ref 계보(ANALYZING의
transcript 보존 등)는 이 로드 경계가 마감한다(plan.md PR-2). 손상 로우는 조용히 복원되지 않고
CorruptJob으로 fail-loud.

## StoragePort를 두지 않은 이유 (YAGNI/altitude)
전사·결과 ref는 로우의 TEXT 컬럼에 그대로 둔다 — 별도 blob 스토어 추상은 실 오디오/S3가 있는
실 인제스트(크레덴셜 확보 후) 전엔 소비자가 없다. transitions 감사 테이블도 같은 이유로 미도입
(버전 컬럼이 낙관적 동시성·재시작 복원을 이미 충족 — 감사로그 리더는 PR5에서). write-only 死코드
회피는 PR1이 RETRYING을 뺀 것과 같은 판단.
"""

from __future__ import annotations

import sqlite3

from pipeline_core.state import CorruptJob, Job, validate_persisted


class StaleVersionError(RuntimeError):
    """낙관적 동시성 충돌 — expected_version이 저장본과 불일치(오래된 읽기/동시 수정).

    RETRYING처럼 지금 안 쓰여도 되는 게 아니라, stage 5 멀티워커 전환의 exactly-once 쓰기가
    의존할 seam이다. 지금 계약으로 박아 그때 재작성을 없앤다."""


class JobRepository:
    """잡 영속화 추상화 — 저장/복원/버전/열거. detect.DetectorPort와 같은 평범한 베이스
    (+NotImplementedError) 스타일(state.Clock/IdSource와도 일관). 구현: InMemory·Sqlite.
    get_store 팩토리로 선택(detect.get_detector 미러링)."""

    def save(self, job: Job, *, expected_version: int = 0) -> int:  # pragma: no cover
        raise NotImplementedError

    def get(self, job_id: str) -> Job | None:                       # pragma: no cover
        raise NotImplementedError

    def version_of(self, job_id: str) -> int:                       # pragma: no cover
        raise NotImplementedError

    def all_jobs(self) -> list:                                     # pragma: no cover
        raise NotImplementedError


class InMemoryJobRepository(JobRepository):
    """프로세스 메모리 fake — 크레덴셜/파일 0으로 계약을 빠르게 검증(리플레이 러너·테스트용).

    Job은 frozen(불변)이라 참조 저장이 안전하다. 저장 경계에서만 validate_persisted를 건다
    (메모리엔 외부 라이터가 없어 스토리지 손상 경로가 없음 — sqlite는 로드에서도 검증)."""

    def __init__(self):
        self._jobs: dict = {}                    # id -> (job, version)

    def save(self, job: Job, *, expected_version: int = 0) -> int:
        validate_persisted(job)                  # 저장 경계 fail-loud(손상 잡 영속화 차단)
        current = self._jobs[job.id][1] if job.id in self._jobs else 0
        if current != expected_version:
            raise StaleVersionError(
                f"{job.id}: expected_version={expected_version}인데 저장본은 {current}"
                f"(오래된 읽기/동시 수정)")
        new_version = current + 1
        self._jobs[job.id] = (job, new_version)
        return new_version

    def get(self, job_id: str) -> Job | None:
        entry = self._jobs.get(job_id)
        return entry[0] if entry is not None else None

    def version_of(self, job_id: str) -> int:
        entry = self._jobs.get(job_id)
        return entry[1] if entry is not None else 0

    def all_jobs(self) -> list:
        return [job for job, _ in self._jobs.values()]


def _to_row(job: Job, version: int) -> dict:
    """Job → 로우 dict. State/failed_from은 문자열(.value)로 저장(rehydrate가 다시 정규화)."""
    return {
        "id": job.id,
        "state": job.state.value,
        "audio_ref": job.audio_ref,
        "transcript_ref": job.transcript_ref,
        "result_ref": job.result_ref,
        "failed_from": job.failed_from.value if job.failed_from is not None else None,
        "attempts": job.attempts,
        "error": job.error,
        "ts": job.ts,
        "version": version,
    }


def _from_row(row: sqlite3.Row) -> Job:
    """로우 → Job(+로드 경계 검증). 손상 state 문자열·계보 위반은 CorruptJob으로 fail-loud."""
    try:
        job = Job(
            id=row["id"], state=row["state"], audio_ref=row["audio_ref"],
            transcript_ref=row["transcript_ref"], result_ref=row["result_ref"],
            failed_from=row["failed_from"], attempts=row["attempts"],
            error=row["error"], ts=row["ts"],
        )
    except ValueError as exc:                    # 알 수 없는 state 문자열 등(__post_init__)
        raise CorruptJob(f"{row['id']}: 잡 로우 손상 — {exc}") from exc
    validate_persisted(job)                      # 계보·빈 문자열·FAILED 정합 전수 검사
    return job


class SqliteJobRepository(JobRepository):
    """stdlib sqlite3 영속 — 실 파일 하나에 스냅샷 저장. 새 인스턴스가 같은 path를 다시 열면
    복원된다(재시작 크래시복구). 커넥션은 연산마다 열고 닫는다 — Windows 파일락 잔류 핸들·
    tmp 정리 실패를 피하고, 단일사용자 MVP엔 오버헤드가 무의미하다.

    낙관적 동시성: SELECT로 현재 버전 확인 후 `UPDATE ... WHERE version=expected`로 조건부 갱신
    → rowcount≠1이면 버전이 이동한 것이라 StaleVersionError. 이 WHERE 가드는 UPDATE 경로의
    '버전 이동' 경쟁만 잡는다 — 같은 id 동시 최초저장(INSERT UNIQUE 경합)·쓰기락 경합은 동시
    라이터가 있는 stage 5에서만 발생하며 그때 하드닝한다(모듈 docstring '범위' 참조)."""

    def __init__(self, path: str):
        if path == ":memory:":
            # 연산별 커넥션 설계와 근본 비호환 — 각 연산이 새 빈 인메모리 DB를 열어 테이블이
            # 사라진다. 프로세스-생명 인메모리가 필요하면 InMemoryJobRepository를 쓴다.
            raise ValueError(
                ":memory:는 SqliteJobRepository(연산별 커넥션)와 비호환입니다 — "
                "인메모리 저장은 InMemoryJobRepository / get_store('memory')를 쓰세요.")
        self._path = path
        conn = self._connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS jobs ("
                " id TEXT PRIMARY KEY,"           # job_id UNIQUE
                " state TEXT NOT NULL,"
                " audio_ref TEXT NOT NULL,"
                " transcript_ref TEXT,"
                " result_ref TEXT,"
                " failed_from TEXT,"
                " attempts INTEGER NOT NULL,"
                " error TEXT,"
                " ts TEXT,"
                " version INTEGER NOT NULL)")
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, job: Job, *, expected_version: int = 0) -> int:
        validate_persisted(job)                  # 저장 경계 fail-loud(로드와 대칭)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT version FROM jobs WHERE id=?", (job.id,)).fetchone()
            current = row["version"] if row is not None else 0
            if current != expected_version:
                raise StaleVersionError(
                    f"{job.id}: expected_version={expected_version}인데 저장본은 {current}"
                    f"(오래된 읽기/동시 수정)")
            new_version = current + 1
            cols = _to_row(job, new_version)
            if row is not None:
                cur = conn.execute(
                    "UPDATE jobs SET state=:state, audio_ref=:audio_ref,"
                    " transcript_ref=:transcript_ref, result_ref=:result_ref,"
                    " failed_from=:failed_from, attempts=:attempts, error=:error,"
                    " ts=:ts, version=:version"
                    " WHERE id=:id AND version=:expected",
                    {**cols, "expected": expected_version})
                if cur.rowcount != 1:            # SELECT↔UPDATE 사이 경쟁 라이터
                    raise StaleVersionError(
                        f"{job.id}: 저장 중 동시 수정 감지(version 이동)")
            else:
                conn.execute(
                    "INSERT INTO jobs (id, state, audio_ref, transcript_ref,"
                    " result_ref, failed_from, attempts, error, ts, version) VALUES"
                    " (:id, :state, :audio_ref, :transcript_ref, :result_ref,"
                    " :failed_from, :attempts, :error, :ts, :version)", cols)
            conn.commit()
            return new_version
        finally:
            conn.close()

    def get(self, job_id: str) -> Job | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        finally:
            conn.close()
        return _from_row(row) if row is not None else None

    def version_of(self, job_id: str) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT version FROM jobs WHERE id=?", (job_id,)).fetchone()
        finally:
            conn.close()
        return row["version"] if row is not None else 0

    def all_jobs(self) -> list:
        # ⚠️ 손상 로우 1개가 CorruptJob으로 열거 전체를 중단시킨다(get과 같은 fail-loud). 복원 시
        #    poison 로우를 격리하고 건강한 잡만 회수할지(skip+quarantine)는 소비자(PR4 오케스트
        #    레이터)가 생길 때 정할 복원 정책 — 지금은 fail-loud 유지(리뷰 defer, 미결 설계).
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM jobs").fetchall()
        finally:
            conn.close()
        return [_from_row(r) for r in rows]


def get_store(name: str, *, path: str | None = None) -> JobRepository:
    """스토어 팩토리 — 'memory'(fake) | 'sqlite'(path 필수). detect.get_detector 미러링.

    입력 검증은 각 생성자 단일 지점에 위임하는 게 원칙이나, sqlite의 path 필수는 팩토리에서
    막는다(생성자 시그니처상 path 없는 SqliteJobRepository 자체가 성립 불가라 여기가 단일 지점)."""
    if name == "memory":
        return InMemoryJobRepository()
    if name == "sqlite":
        if not path:
            raise ValueError("sqlite 스토어는 path가 필요합니다.")
        return SqliteJobRepository(path)
    raise ValueError(f"알 수 없는 스토어: {name!r} (memory | sqlite)")

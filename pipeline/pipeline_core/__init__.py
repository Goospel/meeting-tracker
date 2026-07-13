"""meeting-tracker 단일사용자 파이프라인 (제품 코어, Python 우선 MVP).

업로드→STT→분석→저장을 오케스트레이션 상태머신으로 관통한다. benchmarks/가 '측정'이라면
이 패키지는 '실제 제품'이다 — 구축순서 3단계.

벤치 3종의 DNA를 그대로 계승한다: 런타임 의존성 0(stdlib만), 포트 + 리플레이 어댑터
(크레덴셜 0 관통 + 실 어댑터는 생성 시점 단일 게이트), 순수 결정적 코어, fail-loud, TDD.

모듈(구축 순서):
  state        상태머신 순수 코어 — 상태·전이·불변식·멱등·실패/재시도, IO 0 (+계보 로드검증)
  repository   영속화 (이 PR) — JobRepository(InMemory fake + stdlib sqlite3), 낙관적 동시성
  stt          SttPort — ReplaySttPort(크레덴셜0) + ClovaSttPort(게이트)
  ingest       transcript_to_meeting — 원시 STT 전사 → 감지 가능한 meeting dict
  orchestrator run_pipeline — 위를 구동, 분석 스텝은 detect_bench.run_detection 재사용
  run          단일사용자 CLI 진입점
"""

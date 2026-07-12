"""bare `pytest` 실행 지원 — pytest가 이 conftest의 디렉터리를 sys.path에 넣어
detect_bench 패키지를 미설치 상태에서도 import할 수 있게 한다.
(`python -m pytest`는 CWD 삽입으로 이미 동작했지만 bare `pytest`는 수집 전멸했음 — 리뷰2.)
"""

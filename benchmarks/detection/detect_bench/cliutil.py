"""CLI 공용 유틸 — detect_bench 안의 모든 CLI가 공유(복붙 드리프트 방지).

[3R] T-027 reconfigure 블록이 repo에 5벌 복붙돼 있었고 그중 1벌(stt report.py)은 이미
stdout만 처리하는 변형으로 드리프트했다 — 같은 패키지 안에서라도 단일 출처로 묶는다.
"""

from __future__ import annotations

import sys


def force_utf8_stdio() -> None:
    """Windows cp949 콘솔에서 한글 출력 UnicodeEncodeError 회피 (T-027).

    stdout/stderr **둘 다** — 하나만 하면 에러 경로에서 재발한다(드리프트 실례).
    reconfigure가 없거나(비-TextIOWrapper 스트림) 실패해도 조용히 통과."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

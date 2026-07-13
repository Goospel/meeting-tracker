"""패키지·크로스패키지 import 배선 고정.

pipeline_core 자체와, 오케스트레이터(PR4)가 재사용할 detect_bench가 import 가능한지를
지금 테스트로 못박아, 이후 PR이 import 단계에서 조용히 깨지지 않게 한다(conftest.py 참조).
분석 스텝은 detect_bench.run_detection을 재구현 없이 그대로 호출할 계획이다.
"""


def test_pipeline_core_importable():
    import pipeline_core  # noqa: F401
    from pipeline_core.state import State, advance  # noqa: F401


def test_detect_bench_importable_via_conftest_wiring():
    # PR4 분석 스텝이 재사용할 감지 어댑터 — 지금은 import 가능성만 고정(호출은 안 함).
    from detect_bench.detect import get_detector, run_detection  # noqa: F401

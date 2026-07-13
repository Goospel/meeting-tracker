"""패키지·크로스패키지 import 배선 고정.

pipeline_core 자체와, 오케스트레이터(PR4)가 재사용할 detect_bench가 import 가능한지를
지금 테스트로 못박아, 이후 PR이 import 단계에서 조용히 깨지지 않게 한다(conftest.py 참조).
분석 스텝은 detect_bench.run_detection을 재구현 없이 그대로 호출할 계획이다.

⚠️ 범위: 이 배선은 **pytest 시점**에서만 성립한다(conftest.py가 sys.path에 얹는다). PR4의
   실 오케스트레이터/CLI는 pytest 밖에서 도므로 detect_bench가 그대로는 import되지 않는다 —
   프로덕션 import 경로(패키지 설치 / 경로 의존성 선언)는 PR4에서 별도로 못박는다. 여기서는
   '재사용 대상이 존재하고 배선 경로로 해석된다'만 고정한다.
"""

from pathlib import Path


def test_pipeline_core_importable():
    import pipeline_core  # noqa: F401
    from pipeline_core.state import State, advance  # noqa: F401


def test_detect_bench_importable_via_conftest_wiring():
    # PR4 분석 스텝이 재사용할 감지 어댑터 — import 가능성 + 그 해석이 실제로
    # conftest가 얹은 benchmarks/detection 경로로 가는지까지 고정(다른 경로 우연 해석 방지).
    import detect_bench.detect as detect
    from detect_bench.detect import get_detector, run_detection  # noqa: F401

    resolved = Path(detect.__file__).resolve()
    expected_root = Path(__file__).resolve().parents[2] / "benchmarks" / "detection"
    assert expected_root in resolved.parents, (
        f"detect_bench가 배선 경로가 아닌 {resolved}에서 해석됨 — conftest sys.path 배선 확인"
    )

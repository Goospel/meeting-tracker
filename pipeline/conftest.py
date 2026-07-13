"""pipeline 테스트 import 배선.

두 가지를 sys.path에 얹는다:
  1) pipeline/(이 conftest의 디렉터리) — pipeline_core를 미설치 상태로 import 가능하게
     (bench_stats/detect_bench/stt_bench와 같은 패턴; bare `pytest`도 수집되게 명시 삽입).
  2) benchmarks/detection/ — 오케스트레이터(PR4)가 재사용할 detect_bench(run_detection 등)를
     import 가능하게. 제품 파이프라인의 '분석' 스텝은 감지 채점 벤치의 어댑터를 **재구현 없이
     그대로 호출**한다. 이 배선을 PR1에서 테스트(test_wiring)로 고정해 PR3~4가 import 단계에서
     깨지지 않게 한다.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parent / "benchmarks" / "detection"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

"""pipeline 테스트 import 배선.

두 가지를 sys.path에 얹는다:
  1) pipeline/(이 conftest의 디렉터리) — pipeline_core를 미설치 상태로 import 가능하게
     (bench_stats/detect_bench/stt_bench와 같은 패턴; bare `pytest`도 수집되게 명시 삽입).
     **맨 앞(insert 0)** — pipeline_core가 최우선 해석되게.
  2) benchmarks/detection/ — 오케스트레이터(PR4)가 재사용할 detect_bench(run_detection 등)를
     import 가능하게. 제품 파이프라인의 '분석' 스텝은 감지 채점 벤치의 어댑터를 **재구현 없이
     그대로 호출**한다. 이 배선을 PR1에서 테스트(test_wiring)로 고정해 PR3~4가 import 단계에서
     깨지지 않게 한다. **맨 뒤(append)** — 이 디렉터리엔 패키지가 아닌 top-level `tests/`·
     `fixtures/`·`measurements/`·`conftest.py`가 있어, 앞에 넣으면 동명 모듈을 가린다(shadowing).
     맨 뒤면 유일 패키지인 detect_bench만 끌어오고 그 top-level는 다른 것을 못 가린다.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_DETECT = _HERE.parent / "benchmarks" / "detection"

if _HERE.is_dir() and str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if _DETECT.is_dir() and str(_DETECT) not in sys.path:
    sys.path.append(str(_DETECT))

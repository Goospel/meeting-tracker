"""회의 흐름/모순 감지 품질 벤치마크 (meeting-tracker 2단계).

완벽한 전사본을 입력으로 줬을 때 Claude 모순감지가 얼마나 맞히는가 —
감지 4종(모순/번복/미해결/재논의)의 per-type precision/recall/F1을 잰다.

stage-1(stt-bench)과 같은 철학: 순수·결정적·런타임 의존성 0 채점기 + TDD.
mock 예측으로 크레덴셜 없이 채점기 자체를 end-to-end 검증한다.
"""

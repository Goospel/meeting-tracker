"""quote grounding — 부분일치 · 토큰 Jaccard 폴백 · ungrounded 판정."""

from detect_bench.grounding import ground_quote, resolve_flag_segments
from detect_bench.labels import FlagType, FlowFlag, Statement, TranscriptSegment


def _tx():
    return [
        TranscriptSegment("s1", "p1", "예산은 삼천만원으로 잡읍시다 봅니다"),
        TranscriptSegment("s2", "p2", "출시는 팔월 셋째 주로 확정합니다"),
    ]


def test_ground_exact_substring():
    assert ground_quote("팔월 셋째 주로 확정합니다", _tx()) == "s2"


def test_ground_prefix_substring():
    assert ground_quote("예산은 삼천만원으로", _tx()) == "s1"


def test_ground_jaccard_fallback_on_reorder():
    # substring 실패(어순 다름)지만 토큰 겹침이 커 Jaccard로 grounding.
    assert ground_quote("삼천만원으로 예산은 잡읍시다", _tx()) == "s1"


def test_ungrounded_returns_none():
    assert ground_quote("완전히 무관한 다른 회사 이야기입니다", _tx()) is None


def test_empty_quote_is_ungrounded():
    assert ground_quote("   ", _tx()) is None


def test_resolve_flag_segments_reports_ungrounded():
    flag = FlowFlag("f", FlagType.CONTRADICTION, [
        Statement("p1", "팔월 셋째 주로 확정합니다"),      # → s2
        Statement("p2", "지구는 둥급니다 회의와 무관"),     # → ungrounded
    ])
    segs, ungrounded = resolve_flag_segments(flag, _tx())
    assert segs == frozenset({"s2"})
    assert len(ungrounded) == 1


# ── 리뷰 회귀: 최밀착 세그먼트 선택 ([3]/[6]) ──────────────────────────────

def test_ground_prefers_exact_over_earlier_substring():
    # 앞 세그먼트가 인용을 substring으로 품어도, 뒤의 완전일치 세그먼트를 골라야 한다.
    tx = [
        TranscriptSegment("s1", "p1", "예산 얘기 잠깐 나왔고 다른 것도 얘기했어요"),
        TranscriptSegment("s2", "p2", "예산 얘기"),
    ]
    assert ground_quote("예산 얘기", tx) == "s2"


def test_ground_prefers_tightest_substring():
    # 완전일치가 없으면, 인용이 가장 큰 비중을 차지하는(최밀착) 세그먼트를 고른다.
    tx = [
        TranscriptSegment("s1", "p1", "출시 일정 얘기 그리고 예산 얘기 잠깐 그리고 마케팅까지"),
        TranscriptSegment("s2", "p2", "예산 얘기 상한선"),
    ]
    assert ground_quote("예산 얘기", tx) == "s2"


# ── 리뷰2 회귀: 절삭 인용 가드 ─────────────────────────────────────────────

def test_punctuation_only_quote_is_ungrounded():
    # [리뷰2]: 구두점뿐인 절삭 인용('...', '.', '?')이 substring으로 아무 세그먼트에나
    # grounding되면 예측 segset을 오염시켜 정타를 FP+FN으로 뒤집는다 → None이어야 한다.
    tx = [TranscriptSegment("s1", "p1", "예산은... 일단 보류합시다. 어떨까요?")]
    assert ground_quote("...", tx) is None
    assert ground_quote(".", tx) is None
    assert ground_quote("?", tx) is None

"""quote grounding — 부분일치 · 토큰 Jaccard 폴백 · ungrounded 판정."""

from detect_bench.grounding import ground_quote, ground_quote_span, resolve_flag_segments
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


# ── 실측 전 보강 ①: 인접 세그먼트 걸친 인용 (multi-segment grounding) ──────────
# 인용이 두 세그먼트 경계를 걸치면 단일-세그먼트 3방법(완전일치·최밀착·Jaccard)이 모두
# 실패해 통째로 '할루시'로 오분류된다. 경계 매칭은 STT가 한 화자의 발화를 쪼갠 경우만
# 참이므로 **같은 화자** 연속 세그먼트에 한정한다(교차화자 스티칭 = 실재 연속발화 아님).

def _tx_span():
    return [
        TranscriptSegment("s1", "p1", "이건 확정입시다.", 690),
        TranscriptSegment("s2", "p1", "확정이면 저는 리셀러들한테 자료 뿌려야 해요.", 760),
    ]


def test_ground_span_across_two_segments():
    span = ground_quote_span("확정입시다. 확정이면 저는 리셀러들한테", _tx_span(), speaker="p1")
    assert span == frozenset({"s1", "s2"})


def test_single_segment_quote_does_not_expand_to_span():
    # 한 세그먼트 안에 온전히 든 인용은 그 세그먼트 하나로만(불필요한 span 확장 금지).
    span = ground_quote_span("리셀러들한테 자료 뿌려야", _tx_span(), speaker="p1")
    assert span == frozenset({"s2"})


def test_span_grounding_rejects_true_hallucination():
    span = ground_quote_span("전혀 나온 적 없는 완전한 유령 문장입니다", _tx_span(), speaker="p1")
    assert span == frozenset()


def test_resolve_flag_segments_grounds_boundary_quote():
    flag = FlowFlag("f", FlagType.CONTRADICTION,
                    [Statement("p1", "확정입시다. 확정이면 저는 리셀러들한테", time_sec=690)])
    segs, ungrounded = resolve_flag_segments(flag, _tx_span())
    assert segs == frozenset({"s1", "s2"}) and ungrounded == []


def test_span_rejects_cross_speaker_stitch():
    # [리뷰] 서로 다른 화자의 '앞 끝말 + 뒷 첫말' 파편은 실재 연속발화가 아니다 → grounding 거부.
    tx = [TranscriptSegment("s0", "p1", "그건 좀 아닌 것 같아요", 10),
          TranscriptSegment("s1", "p2", "그래도 진행하시죠", 20)]
    assert ground_quote_span("같아요 그래도", tx, speaker="p1") == frozenset()


def test_span_prefers_hinted_window_over_earlier_same_content():
    # [리뷰 #1] 같은 토큰열이 두 곳에 있으면(선점 위험), time 힌트로 올바른 창을 고른다.
    tx = [TranscriptSegment("s0", "p1", "알파", 10),
          TranscriptSegment("s1", "p1", "베타", 12),
          TranscriptSegment("s2", "p1", "감마", 14),
          TranscriptSegment("s3", "p2", "중간 발언", 50),
          TranscriptSegment("s5", "p1", "알파", 100),
          TranscriptSegment("s6", "p1", "베타 감마 델타", 102)]
    # 진짜로 걸친 창은 s0/s1/s2(size 3, t~12). size 2 [s5,s6]가 선점하면 안 됨.
    assert ground_quote_span("알파 베타 감마", tx, speaker="p1", time_sec=12) == frozenset({"s0", "s1", "s2"})


def test_span_normalization_symmetry_internal_spaces():
    # [리뷰 #4] 세그먼트 내부 다중 공백이 있어도 단일 세그먼트 부분 인용은 그 세그먼트에 grounding.
    tx = [TranscriptSegment("s1", "p1", "오늘   회의   시작   합니다   자   그러면   바로", 10),
          TranscriptSegment("s2", "p1", "네 알겠습니다", 20)]
    assert ground_quote_span("회의 시작", tx, speaker="p1") == frozenset({"s1"})


def test_boundary_quote_prefers_span_over_fuzzy_single():
    # [리뷰 #2] 토큰이 한 세그먼트에 몰려 단일 Jaccard가 통과해도, 경계 인용은 span으로 확장.
    tx = [TranscriptSegment("s1", "p1", "확정합니다 정말로 확실히 그렇게", 10),
          TranscriptSegment("s2", "p1", "네 동의", 20)]
    assert ground_quote_span("확정합니다 정말로 확실히 그렇게 네", tx, speaker="p1") == frozenset({"s1", "s2"})


def test_string_time_hint_does_not_crash():
    # [리뷰1 #5] 예측의 비숫자 time_sec("00:10")가 grounding 힌트 산술에서 크래시하면 안 된다.
    tx = [TranscriptSegment("s1", "p1", "네 좋습니다 그렇게 하죠", 10),
          TranscriptSegment("s2", "p2", "네 좋습니다 그렇게 하죠", 500)]
    # 크래시 없이 grounding(문자열 힌트는 무시 → 결정적 첫 출현).
    assert ground_quote("네 좋습니다 그렇게 하죠", tx, speaker="p1", time_sec="00:10") == "s1"


# ── 리뷰2: span 재설계 — 창 화자 동질성은 전사로 판정, 모호하면 추측 안 함 ──────

def test_span_grounds_despite_pred_speaker_mismatch():
    # [리뷰2 #1/#8] 창 화자 동질성은 전사(세그먼트끼리)로 판정 — 신뢰 불가한 예측 화자 라벨이
    # 전사 id와 다르거나(이름표기) 비어도, 유일한 같은-화자 경계 창이면 grounding해야 한다.
    tx = [TranscriptSegment("s1", "p1", "이건 확정입시다.", 690),
          TranscriptSegment("s2", "p1", "확정이면 저는 리셀러들한테 자료 뿌려야 해요.", 760)]
    q = "확정입시다. 확정이면 저는 리셀러들한테"
    assert ground_quote_span(q, tx, speaker="김대표") == frozenset({"s1", "s2"})   # 이름≠id
    assert ground_quote_span(q, tx, speaker="") == frozenset({"s1", "s2"})          # 화자 생략


def test_span_refuses_when_ambiguous_no_hint():
    # [리뷰2 #2] 같은 화자 창이 둘 이상이고 위치 힌트(time)가 없으면, 틀린 창에 추측 귀속하느니
    # grounding하지 않는다(벤치 점수 조용한 오염 방지). 다른 화자 sX가 두 그룹을 갈라 각 그룹이
    # 독립 창; 어느 세그먼트도 단일 퍼지매칭되지 않게 구성(순수 경계 애매).
    tx = [TranscriptSegment("s0", "p1", "알파", 10),
          TranscriptSegment("s1", "p1", "베타", 12),
          TranscriptSegment("s2", "p1", "감마", 14),
          TranscriptSegment("sX", "p2", "다른 화자 중간 발언", 50),
          TranscriptSegment("s5", "p1", "시작 알파 베타", 100),
          TranscriptSegment("s6", "p1", "감마 델타 끝", 102)]
    assert ground_quote_span("알파 베타 감마", tx, speaker="p1") == frozenset()
    # time 힌트가 있으면 최근접 창으로 확정.
    assert ground_quote_span("알파 베타 감마", tx, speaker="p1", time_sec=12) == frozenset({"s0", "s1", "s2"})


def test_fuzzy_single_not_hijacked_by_unrelated_span():
    # [리뷰2 #6] tier-2 퍼지 단일이 이미 정답 세그먼트를 가리키면, 그 세그먼트를 포함하지 않는
    # 무관한 같은-화자 인접쌍 span이 grounding을 가로채면 안 된다.
    tx = [TranscriptSegment("s3", "p1", "예산 초과 문제 우리가 다시 검토", 100),
          TranscriptSegment("s4", "p2", "중간 발언", 150),
          TranscriptSegment("s5", "p1", "예산 초과 문제", 200),
          TranscriptSegment("s6", "p1", "다시 검토 그래서", 210)]
    assert ground_quote_span("예산 초과 문제 다시 검토", tx, speaker="p1") == frozenset({"s3"})


def test_span_grounds_midrun_boundary_in_long_same_speaker_run():
    # [리뷰3 #HIGH] STT가 한 화자를 3+ 세그먼트로 쪼갠 런에서 mid-run 경계 인용이, 상위집합 창을
    # '별개 후보'로 잘못 세어 모호 판정돼 드롭되면 안 된다 → 최소 커버 창으로 축약.
    tx = [TranscriptSegment("s1", "A", "그래서 우리가", 10),
          TranscriptSegment("s2", "A", "결정한 내용은", 12),
          TranscriptSegment("s3", "A", "예산을 삭감하고", 14),
          TranscriptSegment("s4", "A", "인원을 늘리는", 16)]
    assert ground_quote_span("삭감하고 인원을", tx, speaker="A") == frozenset({"s3", "s4"})


# ── 실측 전 보강 ②: 반복 발화 speaker/time 힌트로 정확한 출현 귀속 ────────────
# 동일 텍스트가 두 세그먼트에 나오면 grounding이 항상 첫 출현을 골라, 두 번째를 가리키는
# 정당한 골든이 게이트에서 거부된다. statement의 speaker/time 힌트로 올바른 출현을 고른다.

def _tx_repeat():
    return [
        TranscriptSegment("s1", "p1", "네 좋습니다 그렇게 하죠", 10),
        TranscriptSegment("s2", "p2", "네 좋습니다 그렇게 하죠", 500),
    ]


def test_repeat_disambiguated_by_speaker_and_time():
    assert ground_quote("네 좋습니다 그렇게 하죠", _tx_repeat(),
                        speaker="p2", time_sec=500) == "s2"


def test_repeat_without_hint_is_deterministic_first():
    # 힌트 없으면 첫 출현으로 결정적(순서 불변 보장 — 기존 동작 보존).
    assert ground_quote("네 좋습니다 그렇게 하죠", _tx_repeat()) == "s1"


def test_resolve_uses_statement_speaker_time_for_repeat():
    flag = FlowFlag("f", FlagType.REVERSAL,
                    [Statement("p2", "네 좋습니다 그렇게 하죠", time_sec=500)])
    segs, _ = resolve_flag_segments(flag, _tx_repeat())
    assert segs == frozenset({"s2"})


# ── 리뷰4(xhigh): 힌트 산술·창 기하 확정 결함 수정 ────────────────────────────

def test_nan_time_hint_does_not_kill_unique_window():
    # [리뷰4 G2a] NaN은 isinstance 숫자 가드를 통과하고 ==비반사성으로 최근접 필터를
    # 전멸시킨다 — 비정상 힌트는 '무시'여야지 유일 창 grounding을 죽이면 안 된다.
    tx = [TranscriptSegment("s1", "p1", "이건 확정입시다.", 690),
          TranscriptSegment("s2", "p1", "확정이면 저는 리셀러들한테 자료 뿌려야 해요.", 760)]
    span = ground_quote_span("확정입시다. 확정이면 저는 리셀러들한테", tx,
                             speaker="p1", time_sec=float("nan"))
    assert span == frozenset({"s1", "s2"})


def test_nonnumeric_start_sec_loses_to_matching_time_hint():
    # [리뷰4 G1] start_sec이 비숫자/None인 세그먼트가 거리 0.0(최우선)이 되어, 숫자 힌트가
    # 정확히 가리키는 세그먼트를 이기면 안 된다(span 경로의 inf와 정합).
    tx = [TranscriptSegment("s1", "p1", "네 좋습니다 그렇게 하죠", None),
          TranscriptSegment("s2", "p1", "네 좋습니다 그렇게 하죠", 500)]
    assert ground_quote("네 좋습니다 그렇게 하죠", tx, time_sec=499) == "s2"
    tx2 = [TranscriptSegment("s1", "p1", "네 좋습니다 그렇게 하죠", "690"),
           TranscriptSegment("s2", "p1", "네 좋습니다 그렇게 하죠", 500)]
    assert ground_quote("네 좋습니다 그렇게 하죠", tx2, time_sec=499) == "s2"


def test_tier2_tie_inside_window_expands_to_span():
    # [리뷰4 G5] tier-2 동점(J=0.6 vs 0.6)에서 _pick이 창 밖 이른 후보를 골라도, 동점 후보
    # 중 하나가 verbatim 창 안에 있으면 span으로 확장해야 한다(첫 출현 오귀속 방지).
    tx = [TranscriptSegment("s1", "p1", "예산 초과 문제", 100),
          TranscriptSegment("s2", "p2", "중간 발언", 150),
          TranscriptSegment("s5", "p1", "예산 초과 문제", 200),
          TranscriptSegment("s6", "p1", "다시 검토 그래서", 210)]
    assert ground_quote_span("예산 초과 문제 다시 검토", tx, speaker="p1") == frozenset({"s5", "s6"})


def test_whitespace_only_segment_does_not_break_window():
    # [리뷰4 G3] 공백-only 세그먼트(STT 침묵)가 창 concat에 이중 공백을 만들어 정당한
    # 경계 인용이 할루시로 떨어지면 안 된다.
    tx = [TranscriptSegment("s1", "p1", "알파", 10),
          TranscriptSegment("s2", "p1", "   ", 12),
          TranscriptSegment("s3", "p1", "베타", 14)]
    assert ground_quote_span("알파 베타", tx, speaker="p1") == frozenset({"s1", "s2", "s3"})


def test_blank_speaker_labels_refuse_window_stitching():
    # [리뷰4 G4] 화자 라벨이 전부 빈 문자열/부재면 ''=='' 로 모든 인접쌍이 '같은 화자'가 되어
    # 교차화자 스티칭 거부가 무력화된다 — 동질성 판정 불가면 보수적으로 창을 만들지 않는다.
    tx = [TranscriptSegment("s0", "", "그건 좀 아닌 것 같아요", 10),
          TranscriptSegment("s1", "", "그래도 진행하시죠", 20)]
    assert ground_quote_span("같아요 그래도", tx) == frozenset()

    class _Bare:                            # 덕타이핑 — speaker 속성 자체가 없는 세그먼트
        def __init__(self, sid, text):
            self.segment_id, self.text = sid, text
    bare = [_Bare("b0", "그건 좀 아닌 것 같아요"), _Bare("b1", "그래도 진행하시죠")]
    assert ground_quote_span("같아요 그래도", bare) == frozenset()


def test_window_time_distance_uses_nearest_segment_not_first():
    # [리뷰4 G2b] 힌트가 진짜 창의 '뒤쪽' 세그먼트 시각을 가리켜도, 창 거리는 첫 세그먼트가
    # 아니라 창 내 최근접 세그먼트 기준이어야 한다.
    tx = [TranscriptSegment("s1", "p1", "알파", 100),
          TranscriptSegment("s2", "p1", "베타 감마", 160),
          TranscriptSegment("sX", "p2", "중간", 120),
          TranscriptSegment("s7", "p1", "알파", 150),
          TranscriptSegment("s8", "p1", "베타 감마", 155)]
    assert ground_quote_span("알파 베타", tx, speaker="p1", time_sec=160) == frozenset({"s1", "s2"})


def test_golden_single_mode_does_not_span_expand():
    # [리뷰4 G7] 골든 경로(span=False)는 main 의미론(단일 세그먼트 + 힌트) — tier-2 퍼지
    # 골든 인용이 이웃 세그먼트로 부풀어 validate/채점 segset을 오염시키면 안 된다.
    tx = [TranscriptSegment("s5", "p1", "예산 초과 문제 우리가", 100),
          TranscriptSegment("s6", "p1", "다시 검토합시다", 110)]
    flag = FlowFlag("f", FlagType.UNRESOLVED,
                    [Statement("p1", "예산 초과 문제 우리가 다시", time_sec=100)])
    segs_single, ung_single = resolve_flag_segments(flag, tx, span=False)
    assert segs_single == frozenset({"s5"}) and ung_single == []
    segs_span, _ = resolve_flag_segments(flag, tx)      # 예측 경로(기본)는 기존대로 확장
    assert segs_span == frozenset({"s5", "s6"})


def test_empty_quote_is_not_hallucination():
    # [리뷰4 G11] 강등된 빈 인용(quote:null)은 '전사에 없는 인용(할루시)'이 아니라
    # '인용 자체가 없음' — ungrounded 목록에 들어가면 안 된다.
    tx = [TranscriptSegment("s1", "p1", "실재하는 발언 하나", 10)]
    flag = FlowFlag("f", FlagType.CONTRADICTION, [Statement("p1", "", None)])
    segs, ungrounded = resolve_flag_segments(flag, tx)
    assert segs == frozenset() and ungrounded == []

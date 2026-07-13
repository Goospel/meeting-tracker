"""골든 회의 3건째 — 경계 span·tier2 하드케이스(결제 시스템 장애 회고).

골든 1·2는 모든 인용이 tier1 부분일치 + time 분해 한 경로에 몰려 있어, **경계 span
grounding**과 **tier2 퍼지 rescue**가 채점 파이프라인에서 발화한 적이 없다(그 기전들은
test_grounding/test_score의 합성 단위 케이스만 커버). 이 세 번째 골든은 **인접 동일화자
세그먼트(STT가 한 발화를 쪼갠 것을 모사)**를 넣어 그 두 경로를 채점 경로에서 실제로 스트레스한다:

  - 경계 span: f1의 첫 진술이 s6·s7(같은 화자 p2 연속)에 쪼개짐 → 예측은 경계를 걸친 인용
    하나로 내고(span으로 {s6,s7} 회수), 골든은 span=False라 세그먼트별로 쪼개 라벨한다.
    양쪽이 같은 segset을 내야 매칭(채점에서 span을 타는 첫 골든).
  - tier2: f4(재논의)를 3세그({s20,s21,s22})로 두고, 오염 예측 cp3이 s20(tier1)+s21(tier2
    재정렬)만 커버(s22 누락) → J=2/3로 매칭하되 **tier2가 죽으면 J=1/3<0.5로 매칭이 소실**된다.
    즉 tier2가 채점 경로에서 판별적(load-bearing)이다(적대적 리뷰 1: 2세그면 tier1 하나로
    J=0.5 문턱을 충족해 tier2가 잉여였음 → 3세그로 교정).

**스코프 한계 — plan.md line 67의 5R 보류 2건을 pin(재설계 아님)**: 채점 의미론 변경은
실측 데이터 확보 후로 미뤄졌고, 이 골든은 현행 동작을 스트레스·고정만 한다.
  - gap ① 모호성 정책 비대칭: 같은 모호 인용이 STT 분할 여부만으로 단일 경로(첫 출현 추측)와
    스팬 경로(거부)로 갈린다 — test_gap1_*가 현행 비대칭을 정확값으로 고정.
  - gap ② 경계 퍼지 tier 부재: 창 매칭이 verbatim 전용이라 경계 인용 한 단어가 의역되면 전량
    소실(할루시로 오라벨) — 단일 세그먼트 의역은 tier2로 구제되는데도. test_gap2_*가 고정.
"""

import json
from collections import Counter, namedtuple
from pathlib import Path

import pytest

from detect_bench.detect import build_detection_prompt, run_detection, ReplayDetectorPort
from detect_bench.grounding import ground_quote, ground_quote_span, resolve_flag_segments
from detect_bench.labels import (
    FlagType,
    load_meeting,
    load_pred_flags,
    pred_flags_from_items,
    validate_golden,
)
from detect_bench.score import score_detection

FIX = Path(__file__).resolve().parent.parent / "fixtures"
GOLDEN = FIX / "golden" / "payments_postmortem.json"
FAITHFUL = FIX / "pred" / "payments_postmortem.faithful.json"
CONTAMINATED = FIX / "pred" / "payments_postmortem.contaminated.json"
RESPONSE = FIX / "response" / "payments_postmortem.claude.txt"

# f1 첫 진술이 STT로 s6→s7에 쪼개진 경계 인용(어느 단일 세그먼트에도 온전히 들지 않음).
BOUNDARY_Q = "결제 승인 API 타임아웃 급증이 외부 PG사 응답 지연 때문"

_Seg = namedtuple("_Seg", "segment_id speaker text start_sec")


def _g():
    return load_meeting(GOLDEN)


# ── 골든 구조·검증 게이트 ──────────────────────────────────────────────────

def test_golden3_validates():
    # 결정적 게이트 — 경계 인용을 세그먼트별로 쪼개 라벨했으니 정/역 grounding 일관성이 통과해야.
    assert validate_golden(_g()) is True


def test_golden3_shape_and_type_mix():
    m = _g()
    assert len(m["transcript"]) == 27
    assert len(m["flags"]) == 5
    c = Counter(f.type for f in m["flags"])
    assert c[FlagType.CONTRADICTION] == 1   # 모순 1건 (f1, 경계 span)
    assert c[FlagType.REVERSAL] == 1        # 번복 1건 (f2)
    assert c[FlagType.UNRESOLVED] == 2      # 미해결 2건 (f3·f5)
    assert c[FlagType.REDISCUSSION] == 1    # 재논의 1건 (f4)


def test_adjacent_same_speaker_runs_present():
    # 이 골든의 핵심 재료 — STT 분할을 모사한 인접 동일화자 런.
    seg = {s.segment_id: s for s in _g()["transcript"]}
    assert seg["s6"].speaker == seg["s7"].speaker == "p2"                 # f1 첫 진술 2분할
    assert seg["s17"].speaker == seg["s18"].speaker == seg["s19"].speaker == "p3"  # 3분할 런


# ── 경계 span: 채점 경로에서 발화하는 첫 골든 ───────────────────────────────

def test_boundary_quote_needs_span_not_single():
    # 경계 인용은 어느 단일 세그먼트에도 온전히 안 들어 단일 grounding은 None이어야 한다
    # (그래야 span 경로가 실제로 필요함을 증명).
    tx = _g()["transcript"]
    assert ground_quote(BOUNDARY_Q, tx, speaker="p2") is None
    assert ground_quote_span(BOUNDARY_Q, tx, speaker="p2", time_sec=200) == frozenset({"s6", "s7"})


def test_golden_labels_boundary_per_segment_but_pred_spans_one_quote():
    # 골든(span=False)은 경계 진술을 s6·s7 두 statement로 쪼개 라벨하고, 예측은 경계를 걸친
    # 인용 하나로 낸다 — 양쪽이 같은 segset {s6,s7,s14}로 수렴해야 매칭된다.
    m = _g()
    f1 = next(f for f in m["flags"] if f.flag_id == "f1")
    g_segs, g_ung = resolve_flag_segments(f1, m["transcript"], span=False)
    assert g_ung == [] and g_segs == frozenset({"s6", "s7", "s14"})
    # s6·s7이 각각 f1을 역참조(세그먼트별 라벨)
    seg = {s.segment_id: s for s in m["transcript"]}
    assert "f1" in seg["s6"].flags and "f1" in seg["s7"].flags

    pf1 = next(p for p in load_pred_flags(FAITHFUL) if p.flag_id == "pf1")
    p_segs, p_ung = resolve_flag_segments(pf1, m["transcript"])   # 예측 경로(span=True)
    assert p_ung == [] and p_segs == frozenset({"s6", "s7", "s14"})
    assert any(st.quote == BOUNDARY_Q for st in pf1.statements)   # 예측은 경계 인용 하나


def test_contradiction_same_speaker_across_the_split():
    # f1은 모순(같은 화자 자기모순) — 첫 진술이 STT로 쪼개졌어도 화자는 s6·s7·s14 모두 p2.
    f1 = next(f for f in _g()["flags"] if f.flag_id == "f1")
    assert {st.speaker for st in f1.statements} == {"p2"}


def test_rediscussion_is_cross_speaker():
    f4 = next(f for f in _g()["flags"] if f.flag_id == "f4")
    # 3자 이견(p2·p4·p3) — 화자가 모두 달라 모순이 아닌 재논의. 3세그라 tier2가 채점에 판별적.
    assert {st.speaker for st in f4.statements} == {"p2", "p4", "p3"}


# ── 충실한 예측: 경계 span 정탐 포함 완벽 재현 ──────────────────────────────

def test_faithful_scores_perfect():
    s = score_detection(_g(), load_pred_flags(FAITHFUL))
    assert (s.overall.tp, s.overall.fp, s.overall.fn) == (5, 0, 0)
    assert s.type_confusions == [] and s.tainted_matches == []
    assert s.misses == [] and s.false_positives == []
    assert s.localization.tp == 5
    for t in ("모순", "번복", "미해결", "재논의"):
        assert s.per_type[t].recall == 1.0


def test_time_blind_boundary_still_grounds_via_speaker_window():
    # 경계 span은 time 힌트가 없어도 같은-화자 창이 유일하면 grounding된다(반복발화 디코이가
    # 없는 정상 케이스). time을 지워도 f1 정탐이 유지됨을 고정.
    data = json.loads(FAITHFUL.read_text(encoding="utf-8-sig"))
    items = data["flags"] if isinstance(data, dict) else data
    pf1 = next(fl for fl in items if fl.get("id") == "pf1")
    for st in pf1["statements"]:
        st.pop("time_sec", None)
    p1 = next(p for p in pred_flags_from_items(items) if p.flag_id == "pf1")
    segs, ung = resolve_flag_segments(p1, _g()["transcript"])
    assert segs == frozenset({"s6", "s7", "s14"}) and ung == []


# ── 오염된 예측: span/tier2가 유발하는 실패모드 분리 ────────────────────────

def test_contaminated_overall_counts():
    s = score_detection(_g(), load_pred_flags(CONTAMINATED))
    # 정타 2(f1 모순 tainted·f4 재논의 tier2), 가짜 2(cp2 타입혼동·cp4 할루시), 놓친 3(f2·f3·f5)
    assert (s.overall.tp, s.overall.fp, s.overall.fn) == (2, 2, 3)


def test_contaminated_type_confusion_reversal_as_contradiction():
    s = score_detection(_g(), load_pred_flags(CONTAMINATED))
    assert s.localization.tp == 3                 # strict 2 + type-무관 확장 1
    pairs = {(tc.golden_type, tc.pred_type) for tc in s.type_confusions}
    assert pairs == {("번복", "모순")}             # f2를 모순으로 오라벨(cp2)
    assert len(s.type_confusions) == 1


def test_contaminated_tainted_boundary_match():
    # cp1은 경계 인용으로 f1과 정타되지만 할루시 인용 하나를 물고 있음 → tainted로 분리.
    s = score_detection(_g(), load_pred_flags(CONTAMINATED))
    assert any(tm.pred_flag_id == "cp1" for tm in s.tainted_matches)
    assert ("f1", "cp1") in s.matches


def test_contaminated_fp_reasons_and_misses():
    s = score_detection(_g(), load_pred_flags(CONTAMINATED))
    reasons = {fp.flag_id: (fp.reason, fp.type_confused) for fp in s.false_positives}
    assert reasons.get("cp2") == ("unmatched", True)    # grounding됐지만 오타입(번복→모순)
    assert reasons.get("cp4") == ("ungrounded", False)  # 전사에 없는 인용
    misses = {m.flag_id: m.type_confused for m in s.misses}
    assert misses == {"f2": True, "f3": False, "f5": False}  # f2는 localization으론 잡힘


def test_contaminated_tier2_reorder_still_matches():
    # cp3은 f4의 두 번째 근거(s21)를 토큰 재정렬로 인용 — 퍼지 Jaccard(tier2)로 grounding돼
    # {s20,s21}로 f4와 정타(J=2/3). 세 번째 근거 s22는 커버 안 함.
    cp3 = next(p for p in load_pred_flags(CONTAMINATED) if p.flag_id == "cp3")
    segs, ung = resolve_flag_segments(cp3, _g()["transcript"])
    assert segs == frozenset({"s20", "s21"}) and ung == []
    s = score_detection(_g(), load_pred_flags(CONTAMINATED))
    assert ("f4", "cp3") in s.matches


def test_tier2_is_load_bearing_in_scoring():
    # [적대적 리뷰 1] f4가 3세그({s20,s21,s22})이므로 tier2가 채점 경로에서 **판별적**이다:
    # cp3의 tier2 회수(s21)를 죽이면(할루시로 치환) cp3={s20}만 남아 J(f4)=1/3<0.5 → f4 매칭 소실.
    # f4가 2세그였다면 tier1 s20 하나로 J=1/2=0.5 문턱을 충족해 tier2가 죽어도 매칭이 서서 잉여였다.
    data = json.loads(CONTAMINATED.read_text(encoding="utf-8-sig"))
    items = data["flags"] if isinstance(data, dict) else data
    cp3 = next(fl for fl in items if fl.get("id") == "cp3")
    # 두 번째 statement(s21 재정렬 = 유일한 tier2 회수)를 전사에 없는 인용으로 치환
    cp3["statements"][1]["quote"] = "이건 전사에 전혀 없는 완전히 지어낸 근거 문장입니다"
    s = score_detection(_g(), pred_flags_from_items(items))
    assert (s.overall.tp, s.overall.fp, s.overall.fn) == (1, 3, 4)   # f4가 놓침으로 떨어짐
    assert not any(g == "f4" for g, _ in s.matches)


def test_contaminated_scoring_invariant_under_pred_order():
    g, p = _g(), load_pred_flags(CONTAMINATED)
    a = score_detection(g, p)
    b = score_detection(g, list(reversed(p)))
    assert (a.overall.tp, a.overall.fp, a.overall.fn) == (b.overall.tp, b.overall.fp, b.overall.fn)
    assert {m.flag_id for m in a.misses} == {m.flag_id for m in b.misses}
    assert set(a.matches) == set(b.matches)


def test_threshold_and_content_tiebreak_are_load_bearing():
    # [적대적 리뷰 2] 이 골든의 실매칭은 J∈{2/3,1.0}이라 임계 0.5와 동점 내용-타이브레이크가
    # 발화한다 — 두 견고성 가드를 공허하지 않게 현행 동작으로 pin(재설계 아님).
    m = _g()
    # (a) J=0.5 경계: f2({s10,s15}) 중 s10만 커버 → J=1/2. `>=` 임계라 0.5에선 매칭, 0.6에선 FP.
    half = pred_flags_from_items([{"id": "half", "type": "번복",
        "statements": [{"speaker": "p1", "quote": "이번 건은 롤백으로 확정합니다"}]}])
    assert ("f2", "half") in score_detection(m, half, match_threshold=0.5).matches
    s_above = score_detection(m, half, match_threshold=0.6)
    assert ("f2", "half") not in s_above.matches
    assert any(mm.flag_id == "f2" for mm in s_above.misses)

    # (b) 동점(J=2/3) 두 예측이 f4 하나를 두고 경합 → 내용(id) 기준 결정적 승자, 순서 불변.
    #     score.py:128-131 _key가 인덱스 폴백으로 되돌아가면 순서 뒤집기에 승자가 바뀌어 깨진다.
    def mk(pid):
        return {"id": pid, "type": "재논의", "statements": [
            {"speaker": "p2", "quote": "온콜 대응이 느렸던 게 더 큰 문제"},
            {"speaker": "p4", "quote": "온콜은 정상이었고 알림이 안 울린 게 문제"}]}
    sx = score_detection(m, pred_flags_from_items([mk("pmX"), mk("pmY")]))
    sy = score_detection(m, pred_flags_from_items([mk("pmY"), mk("pmX")]))
    assert set(sx.matches) == set(sy.matches)                     # 순서 불변
    assert [p for gg, p in sx.matches if gg == "f4"] == ["pmX"]   # "pmX" < "pmY" → 결정적 승자
    assert sx.overall.fp == 1                                     # 진 예측은 dangling FP


# ── gap ① 모호성 정책 비대칭 (plan.md line 67 — 현행 동작 pin) ──────────────

def test_gap1_ambiguity_asymmetry_single_guesses_span_rejects():
    # 같은 모호 인용이 STT 분할 여부만으로 다르게 채점된다:
    #  - 단일 경로(반복 단일 세그먼트): 힌트 없으면 '첫 출현'을 추측(→ 우연히 TP거나 오귀속 FP)
    #  - 스팬 경로(같은 내용이 두 창에 걸침): 모호하면 거부(→ ungrounded, 할루시로 오라벨)
    # 이 비대칭은 매칭 의미론 변경이라 실측 후 재설계 대상(5R 보류 ①). 여기선 현행을 고정만.
    single_tx = [_Seg("a1", "X", "예산을 대폭 삭감하기로 했습니다", 0),
                 _Seg("a2", "Y", "네 알겠습니다", 10),
                 _Seg("a3", "X", "예산을 대폭 삭감하기로 했습니다", 20)]
    # 반복 단일 세그먼트 + 힌트 없음 → 결정적 첫 출현 추측
    assert ground_quote("예산을 대폭 삭감하기로 했습니다", single_tx) == "a1"

    span_tx = [_Seg("b1", "X", "우리는 다음 분기 예산을", 0),
               _Seg("b2", "X", "대폭 삭감하기로 했습니다", 10),
               _Seg("b3", "Y", "알겠습니다", 20),
               _Seg("b4", "X", "우리는 다음 분기 예산을", 30),
               _Seg("b5", "X", "대폭 삭감하기로 했습니다", 40)]
    boundary = "예산을 대폭 삭감하기로"          # 어느 단일 세그먼트에도 온전히 안 듦(경계)
    assert ground_quote(boundary, span_tx) is None          # 단일로는 안 붙음
    assert ground_quote_span(boundary, span_tx) == frozenset()   # 두 창 모호 → 추측 없이 거부
    # 힌트를 주면 스팬도 갈린다 — 비대칭의 뿌리는 '힌트 부재 시 정책'(추측 vs 거부).
    assert ground_quote_span(boundary, span_tx, time_sec=35) == frozenset({"b4", "b5"})


# ── gap ② 경계 퍼지 tier 부재 (plan.md line 67 — 현행 동작 pin) ─────────────

def test_gap2_boundary_verbatim_grounds_paraphrase_lost():
    # 창 매칭은 verbatim 전용 — 경계 인용 한 단어('두 배'→'세 배')만 의역돼도 창이 전멸해
    # 전량 ungrounded(할루시로 오라벨). 단일 세그먼트 의역은 tier2로 구제되는데도(비대칭).
    # 매칭 의미론 변경(퍼지 창 tier)은 과매칭 위험이 커 실측 후 설계(5R 보류 ②). 현행 고정.
    tx = _g()["transcript"]
    verbatim = "커넥션 풀 상한을 지금보다 두 배로 올리고"     # s17→s18 경계, verbatim
    paraphrase = "커넥션 풀 상한을 지금보다 세 배로 올리고"   # 한 단어만 의역
    assert ground_quote_span(verbatim, tx, speaker="p3", time_sec=710) == frozenset({"s17", "s18"})
    assert ground_quote_span(paraphrase, tx, speaker="p3", time_sec=710) == frozenset()  # 전량 소실
    # 대조 — 단일 세그먼트 의역(토큰 재정렬)은 tier2로 grounding된다.
    reorder = "알림이 안 울린 게 문제였다고 봐요 온콜은 정상이었고 저는 반대로"   # s21 재정렬
    assert ground_quote_span(reorder, tx) == frozenset({"s21"})


# ── 어댑터 관통: 리플레이 포트로 전사→프롬프트→파싱→pred→채점 (크레덴셜 0) ──

def test_prompt_does_not_leak_golden_labels():
    p = build_detection_prompt(_g())
    for f in _g()["flags"]:
        assert f.title not in p
        assert f.explanation not in p
    assert "확정 사항이 흐려짐" not in p   # summary.headline 누출 방지


def test_replay_fixture_grounds_and_scores_perfect():
    # 경계 인용을 담은 캔드 응답을 어댑터 실경로로 관통(전사→프롬프트→파싱→pred→채점).
    resp = RESPONSE.read_text(encoding="utf-8-sig")
    m = _g()
    pred = run_detection(m, ReplayDetectorPort(resp))
    s = score_detection(m, pred)
    assert (s.overall.tp, s.overall.fp, s.overall.fn) == (5, 0, 0)
    assert s.misses == [] and s.false_positives == []
    assert s.type_confusions == [] and s.tainted_matches == []

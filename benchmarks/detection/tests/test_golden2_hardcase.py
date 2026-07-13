"""골든 회의 2건째 — 하드케이스(그린마트 이탈 대응).

첫 골든(luma)은 4유형을 깔끔하게 한 건씩 담은 기준선이다. 이 두 번째 골든은 **순진한
감지기·채점기를 스트레스**하도록 설계된 하드케이스다:
  - 중첩(nested): 한 전사 라인이 두 flag의 근거(s16=모순앵커+미해결, s21=번복+모순)
  - 모순↔번복 근접: 같은 '무상 연장' 어휘/라인(s21)에 번복(f2)과 모순(f3)이 공존
  - 반복 발화 분해: f5 근거 인용이 s5(디코이)·s18에 byte-동일 → time 힌트로만 갈림
  - 교차화자 near-miss: 서로 다른 화자(p2·p3)의 대립은 모순이 아니라 재논의(f5)
  - 같은 type 복수: 모순 2건(f1·f3)·미해결 2건(f4·f6)으로 1:1 그리디 매칭 압박

정답 데이터이므로 내부적으로 완벽히 일관(validate_golden 통과)하며, '하드함'은 정답의
모호함이 아니라 순진한 감지기가 틀리기 쉬움을 뜻한다.

스코프 한계(적대적 리뷰 반영):
  - 이 골든의 grounding은 **tier1 부분일치 + time 분해** 한 경로에 집중돼 있다(전 인용 tier1,
    인접 동일화자 세그먼트가 없어 경계 span·tier2 퍼지 rescue는 발화하지 않음). 그 경로들은
    test_grounding/test_score의 합성 케이스가 커버하며, span/tier2를 실제로 스트레스하는
    골든은 후속 하드케이스로 분리한다.
  - s5/s18 byte-동일 반복은 자연스러운 회의체라기보다 **반복발화 분해를 스트레스하기 위한
    의도적 인공물**이다(디코이 vs 근거를 오직 time으로만 가르는 최악의 케이스).
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from detect_bench.detect import build_detection_prompt, run_detection, ReplayDetectorPort
from detect_bench.grounding import resolve_flag_segments
from detect_bench.labels import (
    FlagType,
    load_meeting,
    load_pred_flags,
    pred_flags_from_items,
    validate_golden,
)
from detect_bench.score import score_detection

FIX = Path(__file__).resolve().parent.parent / "fixtures"
GOLDEN = FIX / "golden" / "greenmart_meeting.json"
FAITHFUL = FIX / "pred" / "greenmart_meeting.faithful.json"
CONTAMINATED = FIX / "pred" / "greenmart_meeting.contaminated.json"
RESPONSE = FIX / "response" / "greenmart_meeting.claude.txt"


def _g():
    return load_meeting(GOLDEN)


# ── 골든 구조·검증 게이트 ──────────────────────────────────────────────────

def test_golden2_validates():
    # 결정적 게이트 — grounding 정/역 일관성이 깨지면 여기서 raise.
    assert validate_golden(_g()) is True


def test_golden2_shape_and_type_mix():
    m = _g()
    assert len(m["transcript"]) == 26
    assert len(m["flags"]) == 6
    c = Counter(f.type for f in m["flags"])
    assert c[FlagType.CONTRADICTION] == 2   # 모순 2건 (f1·f3)
    assert c[FlagType.REVERSAL] == 1        # 번복 1건 (f2)
    assert c[FlagType.UNRESOLVED] == 2      # 미해결 2건 (f4·f6)
    assert c[FlagType.REDISCUSSION] == 1    # 재논의 1건 (f5)


# ── 하드케이스1: 중첩 — 한 라인이 두 flag의 근거 ───────────────────────────

def test_nested_segments_backref_two_flags():
    seg = {s.segment_id: s for s in _g()["transcript"]}
    # s16: 자기모순 앵커(f1)와 미해결 안건(f4)이 한 발언에 공존
    assert set(seg["s16"].flags) == {"f1", "f4"}
    # s21: 번복(f2)과 모순(f3)이 '무상 연장' 같은 라인에 공존
    assert set(seg["s21"].flags) == {"f2", "f3"}


def test_nested_flags_actually_ground_to_shared_segment():
    m = _g()
    by_id = {f.flag_id: f for f in m["flags"]}
    for fid in ("f1", "f4"):
        segs, _ = resolve_flag_segments(by_id[fid], m["transcript"], span=False)
        assert "s16" in segs
    for fid in ("f2", "f3"):
        segs, _ = resolve_flag_segments(by_id[fid], m["transcript"], span=False)
        assert "s21" in segs


# ── 하드케이스2: 반복 발화 분해 — 디코이 vs 근거는 time으로만 갈림 ─────────

def test_repeated_utterance_decoy_is_flagless():
    seg = {s.segment_id: s for s in _g()["transcript"]}
    assert seg["s5"].text == seg["s18"].text   # byte-동일 반복
    assert seg["s5"].flags == ()               # 디코이(초반 진술)는 어떤 flag도 역참조 X
    assert "f5" in seg["s18"].flags            # 근거는 재논의가 봉합 없이 넘어간 s18


def test_f5_grounds_to_s18_not_decoy_via_time_hint():
    m = _g()
    f5 = next(f for f in m["flags"] if f.flag_id == "f5")
    segs, ung = resolve_flag_segments(f5, m["transcript"], span=False)
    assert ung == []
    assert "s18" in segs and "s5" not in segs   # time 힌트가 근거 출현을 s18로 고정


# ── 하드케이스3: 모순 동일화자 불변식 ──────────────────────────────────────

def test_contradiction_flags_are_same_speaker():
    for f in _g()["flags"]:
        if f.type == FlagType.CONTRADICTION:
            assert len({st.speaker for st in f.statements}) == 1


def test_reversal_flag_is_cross_speaker():
    # 번복은 모순과 달리 '확정자 ≠ 번복자'여도 성립 — 화자가 갈려야 모순과 구분된다.
    f2 = next(f for f in _g()["flags"] if f.flag_id == "f2")
    assert len({st.speaker for st in f2.statements}) == 2


# ── 충실한 예측: 완벽 재현 ─────────────────────────────────────────────────

def test_faithful_scores_perfect():
    s = score_detection(_g(), load_pred_flags(FAITHFUL))
    assert (s.overall.tp, s.overall.fp, s.overall.fn) == (6, 0, 0)
    assert s.type_confusions == [] and s.tainted_matches == []
    assert s.misses == [] and s.false_positives == []
    assert s.localization.tp == 6
    for t in ("모순", "번복", "미해결", "재논의"):
        assert s.per_type[t].recall == 1.0


def test_time_blind_faithful_hijacks_to_decoy_segment():
    # 하드케이스 회귀: f5의 time 힌트를 지우면 반복발화 첫 출현(디코이 s5)으로 오귀속된다.
    # [리뷰6] 부등식(tp<6)이 아니라 '하필 s5로 하이재킹된다'는 기전 자체를 정확값으로 고정.
    data = json.loads(FAITHFUL.read_text(encoding="utf-8-sig"))
    items = data["flags"] if isinstance(data, dict) else data
    pf5 = next(fl for fl in items if fl.get("id") == "pf5")
    for st in pf5["statements"]:
        st.pop("time_sec", None)
    preds = pred_flags_from_items(items)
    # (a) 인용은 완벽한데 time만 없으면 근거(s18)가 아니라 디코이(s5)로 붙는다.
    p5 = next(p for p in preds if p.flag_id == "pf5")
    segs, ung = resolve_flag_segments(p5, _g()["transcript"])
    assert segs == frozenset({"s5", "s19"}) and ung == []
    # (b) 그 결과 골든 f5({s18,s19})와 Jaccard 1/3 < 0.5 → 정확히 tp 하나 감소·f5 놓침.
    s = score_detection(_g(), preds)
    assert s.overall.tp == 5
    assert any(m.flag_id == "f5" for m in s.misses)


@pytest.mark.parametrize("t", ["760", "760.0", "12:40", "760초"])
def test_string_time_sec_still_grounds_repeated_utterance(t):
    # 실측 보강[리뷰1]: 실제 Claude가 time을 숫자문자열('760')·시각('12:40')·단위표기('760초')로
    # 내도 f5 반복발화 정탐이 '포맷 사유'로 놓침(FN+FP) 처리되면 안 된다 — 예측 경로가 관용 파싱.
    data = json.loads(FAITHFUL.read_text(encoding="utf-8-sig"))
    pf5 = next(fl for fl in data["flags"] if fl.get("id") == "pf5")
    pf5["statements"][0]["time_sec"] = t          # 반복발화 근거 s18(760초)을 가리키는 힌트
    s = score_detection(_g(), pred_flags_from_items(data["flags"]))
    assert (s.overall.tp, s.overall.fp, s.overall.fn) == (6, 0, 0)


# ── 오염된 예측: 하드케이스가 유발하는 실패모드 분리 ────────────────────────

def test_contaminated_overall_counts():
    s = score_detection(_g(), load_pred_flags(CONTAMINATED))
    # 정타 2(f1 모순·f6 미해결), 가짜 3(cp2·cp3 타입혼동 + halluc1 할루시), 놓친 4(f2·f3·f4·f5)
    assert (s.overall.tp, s.overall.fp, s.overall.fn) == (2, 3, 4)


def test_contaminated_type_confusion_covers_moron_bokbok_and_speaker():
    s = score_detection(_g(), load_pred_flags(CONTAMINATED))
    assert s.localization.tp == 4                 # strict 2 + type-무관 확장 2
    pairs = {(tc.golden_type, tc.pred_type) for tc in s.type_confusions}
    assert ("모순", "번복") in pairs               # '무상 연장' 라인에서 모순↔번복 뒤바꿈 (f3)
    assert ("재논의", "모순") in pairs             # 교차화자 대립을 모순으로 오라벨 (f5)
    assert len(s.type_confusions) == 2


def test_contaminated_nested_unresolved_is_a_clean_miss():
    s = score_detection(_g(), load_pred_flags(CONTAMINATED))
    misses = {m.flag_id: m for m in s.misses}
    assert set(misses) == {"f2", "f3", "f4", "f5"}
    # 같은 라인 s16의 모순(f1)은 잡았지만 공존한 미해결(f4)은 놓침 — 순수 놓침(타입혼동 아님)
    assert misses["f4"].type_confused is False
    assert misses["f2"].type_confused is False
    assert misses["f3"].type_confused is True     # localization으론 찾았으나 라벨 틀림
    assert misses["f5"].type_confused is True


def test_contaminated_fp_reasons_and_tainted_match():
    s = score_detection(_g(), load_pred_flags(CONTAMINATED))
    reasons = {fp.flag_id: fp.reason for fp in s.false_positives}
    assert reasons.get("halluc1") == "ungrounded"   # 전사에 없는 인용
    assert reasons.get("cp2") == "unmatched"         # grounding됐지만 오타입(번복)
    assert reasons.get("cp3") == "unmatched"         # grounding됐지만 오타입(모순)
    # cp1은 f1과 매칭(정타)되지만 할루시 인용 하나를 물고 있음 → tainted로 분리 노출
    assert any(tm.pred_flag_id == "cp1" for tm in s.tainted_matches)


def test_contaminated_scoring_invariant_under_pred_order():
    g, p = _g(), load_pred_flags(CONTAMINATED)
    a = score_detection(g, p)
    b = score_detection(g, list(reversed(p)))
    assert (a.overall.tp, a.overall.fp, a.overall.fn) == (b.overall.tp, b.overall.fp, b.overall.fn)
    assert {m.flag_id for m in a.misses} == {m.flag_id for m in b.misses}
    assert len(a.type_confusions) == len(b.type_confusions)


# ── 어댑터 관통: 리플레이 포트로 전사→프롬프트→파싱→pred→채점 (크레덴셜 0) ──

def test_prompt_does_not_leak_golden_labels():
    # 프롬프트는 전사·참석자만 제시해야 한다 — 정답 flag의 제목/설명·요약이 새면 낙관 편향.
    p = build_detection_prompt(_g())
    for f in _g()["flags"]:
        assert f.title not in p          # 정답 제목 누출 방지
        assert f.explanation not in p    # 정답 설명 누출 방지
    assert "확정 사항이 흐려짐" not in p   # summary.headline 누출 방지


def test_replay_fixture_grounds_and_scores_perfect():
    # 고아 방지 + 하드 골든을 어댑터 실경로로 관통 — 골든/응답 drift를 잡는다.
    # [리뷰9] recall뿐 아니라 precision도 만점임을 고정(가짜/타입혼동 flag가 섞여도 잡히게).
    resp = RESPONSE.read_text(encoding="utf-8")
    m = _g()
    pred = run_detection(m, ReplayDetectorPort(resp))
    s = score_detection(m, pred)
    assert (s.overall.tp, s.overall.fp, s.overall.fn) == (6, 0, 0)
    assert s.misses == [] and s.false_positives == []
    assert s.type_confusions == [] and s.tainted_matches == []


# ── 하드케이스: 같은 type 복수(모순 2)의 1:1 그리디 매칭 실제 스트레스 ─────────

def _flag_stmts(m, fid):
    f = next(x for x in m["flags"] if x.flag_id == fid)
    return [(st.speaker, st.quote, st.time_sec) for st in f.statements]


def test_same_type_greedy_matching_is_1to1_and_order_invariant():
    # [리뷰7/8] '모순 2건'이 실제로 1:1 매칭을 압박하려면 예측이 두 동일-type 골든과 동시에
    # 겹쳐 경합해야 한다. f1·f3(둘 다 모순)의 네 앵커에 모두 grounding되는 예측 둘을 만들면
    # 각 예측이 f1·f3와 J=0.5로 동시 자격 — 그리디가 (a) 각 골든을 한 예측에만 1:1 배정하고
    # (b) 동점을 내용(id) 기준으로 결정해 예측 순서에 불변이어야 한다.
    m = _g()
    anchors = _flag_stmts(m, "f1") + _flag_stmts(m, "f3")   # → {s4,s16,s11,s21}
    def mk(pid):
        return {"id": pid, "type": "모순",
                "statements": [{"speaker": sp, "quote": q, "time_sec": t} for sp, q, t in anchors]}
    s = score_detection(m, pred_flags_from_items([mk("pA"), mk("pB")]))
    assert s.per_type["모순"].tp == 2 and s.per_type["모순"].fn == 0   # 두 모순 골든 다 매칭
    assert {"f1", "f3"} <= {g for g, _ in s.matches}
    s2 = score_detection(m, pred_flags_from_items([mk("pB"), mk("pA")]))
    assert set(s.matches) == set(s2.matches)               # 순서 뒤집어도 내용 기준 동일 매칭


def test_no_evidence_fp_reason_for_empty_quote():
    # [리뷰10] 세 번째 FP reason 'no_evidence'(인용 자체가 빈 문자열로 강등 → grounding 시도 없음)를
    # 하드케이스 스위트에서도 커버 — 예측 강등 경로(null quote → "")가 할루시(ungrounded)와 분리되는지.
    preds = pred_flags_from_items([{"id": "noev", "type": "미해결",
                                    "statements": [{"speaker": "p1", "quote": None}]}])
    fp = {f.flag_id: f for f in score_detection(_g(), preds).false_positives}["noev"]
    assert fp.reason == "no_evidence"
    assert fp.ungrounded_quotes == ()

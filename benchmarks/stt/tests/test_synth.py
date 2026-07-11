"""합성 골든셋 빌더 (Track A) — '스크립트 하나 → 골든 + TTS 매니페스트' 단일 소스.

핵심 메커니즘: 회의를 인라인 마크업 스크립트로 한 번만 작성하면, 빌더가
  (1) CTER 골든 JSON(오프셋·canonical 자동 산출, 검증 게이트 통과 보장)
  (2) TTS 렌더 매니페스트(마크업 제거한 화자별 발화)
를 같은 소스에서 파생한다 → 골든과 렌더 오디오가 어긋날 수 없다.

마크업: [[surface|TYPE]] 또는 [[surface|TYPE|contradiction_key]]
"""

import json
from pathlib import Path

import pytest

from stt_bench.golden import golden_from_data, validate_golden
from stt_bench.report import score_meeting
from stt_bench.synth import build_golden, main, render_manifest

FIX = Path(__file__).resolve().parent.parent / "fixtures"
SCRIPT = FIX / "synth" / "budget_reversal.script.json"
BUILT_GOLDEN = FIX / "golden" / "synth_budget_reversal.json"


def _mini_script():
    return {
        "clip_id": "mini_synth",
        "speakers": [{"id": "p1", "role": "PM"}, {"id": "p2", "role": "재무"}],
        "turns": [
            {
                "segment_id": "s1", "speaker": "p1", "start_sec": 0.0, "end_sec": 4.0,
                "text": "예산은 [[3천만원|AMOUNT|budget_cap]]으로 잡읍시다",
            },
            {
                "segment_id": "s2", "speaker": "p2", "start_sec": 4.0, "end_sec": 8.0,
                "text": "출시는 [[8월 셋째 주|DATE|launch_date]]로 하고 [[세 편|UNIT_QUANTITY]] 만들죠",
            },
            {
                "segment_id": "s3", "speaker": "p1", "start_sec": 9.0, "end_sec": 13.0,
                "text": "다시 보니 [[5천만원|AMOUNT|budget_cap]]까지 필요해요",
            },
        ],
    }


# ── 빌더 단위: 오프셋·canonical 자동 산출 ────────────────────────────────

def test_build_computes_offsets_matching_surface():
    g = build_golden(_mini_script())
    for seg in g["segments"]:
        for e in seg["critical_entities"]:
            assert seg["text"][e["char_start"]:e["char_end"]] == e["surface"]


def test_build_derives_canonical_from_parser():
    g = build_golden(_mini_script())
    ents = {e["entity_id"]: e for seg in g["segments"] for e in seg["critical_entities"]}
    # AMOUNT 값 등가 + 통화 코드
    amt = [e for e in ents.values() if e["type"] == "AMOUNT"]
    assert {e["canonical"]["value"] for e in amt} == {30_000_000, 50_000_000}
    assert all(e["canonical"]["unit"] == "KRW" for e in amt)
    # DATE 구조화
    date = next(e for e in ents.values() if e["type"] == "DATE")
    assert date["canonical"] == {"month": 8, "week_of_month": 3}
    # UNIT_QUANTITY 값+단위
    uq = next(e for e in ents.values() if e["type"] == "UNIT_QUANTITY")
    assert uq["canonical"] == {"value": 3, "unit": "편"}


def test_markup_stripped_from_segment_text():
    g = build_golden(_mini_script())
    for seg in g["segments"]:
        assert "[[" not in seg["text"] and "]]" not in seg["text"] and "|" not in seg["text"]


def test_entity_ids_unique():
    g = build_golden(_mini_script())
    ids = [e["entity_id"] for seg in g["segments"] for e in seg["critical_entities"]]
    assert len(ids) == len(set(ids))


# ── 검증 게이트: 빌더 산출물은 '구성상' validate_golden을 통과한다 ────────

def test_built_golden_passes_validation_gate():
    g = build_golden(_mini_script())
    assert validate_golden(golden_from_data(g)) is True


# ── 심어진 번복: 같은 key·같은 화자, 다른 값 ──────────────────────────────

def test_planted_contradiction_same_key_same_speaker_diff_value():
    g = build_golden(_mini_script())
    cap = [
        (seg["speaker"], e["canonical"]["value"])
        for seg in g["segments"] for e in seg["critical_entities"]
        if e.get("contradiction_key") == "budget_cap"
    ]
    assert len(cap) == 2
    assert {sp for sp, _ in cap} == {"p1"}          # 같은 화자(모순의 정의)
    assert {v for _, v in cap} == {30_000_000, 50_000_000}  # 다른 값


# ── TTS 매니페스트: 마크업 제거, 세그먼트 텍스트와 일치 ───────────────────

def test_render_manifest_strips_and_matches_golden_text():
    script = _mini_script()
    g = build_golden(script)
    manifest = render_manifest(script)
    assert [m["text"] for m in manifest] == [seg["text"] for seg in g["segments"]]
    for m in manifest:
        assert "[[" not in m["text"] and "|" not in m["text"]
        assert m["speaker"] in {"p1", "p2"}


# ── end-to-end 관통: 골든 ↔ 채점기 (크레덴셜 없이) ────────────────────────

def test_faithful_hypothesis_scores_cter_zero():
    script = _mini_script()
    g = build_golden(script)
    golden = golden_from_data(g)
    # 충실한 hyp: 매니페스트 그대로 + 표면형만 다른 값 등가(3천만원→삼천만원)
    hyp_segs = {seg["segment_id"]: seg["text"] for seg in g["segments"]}
    hyp_segs["s1"] = hyp_segs["s1"].replace("3천만원", "삼천만원")
    hyp = {"clip_id": "mini_synth", "provider": "faithful_mock", "segments": hyp_segs}
    m = score_meeting(golden, hyp)
    assert m["per_type"]["AMOUNT"].cter == 0.0
    assert len(m["false_contradiction_candidates"]) == 0


def test_contaminated_hypothesis_flags_sub_at_reversal():
    script = _mini_script()
    g = build_golden(script)
    golden = golden_from_data(g)
    hyp_segs = {seg["segment_id"]: seg["text"] for seg in g["segments"]}
    hyp_segs["s1"] = hyp_segs["s1"].replace("3천만원", "2천만원")  # 오인식 주입
    hyp = {"clip_id": "mini_synth", "provider": "contaminated_mock", "segments": hyp_segs}
    m = score_meeting(golden, hyp)
    assert m["per_type"]["AMOUNT"].sub == 1
    fcs = m["false_contradiction_candidates"]
    assert len(fcs) == 1
    assert fcs[0].ref_value == 30_000_000 and fcs[0].hyp_value == 20_000_000


# ── 커밋된 골든 픽스처는 스크립트에서 '재생성 가능'해야 한다 (무드리프트) ──

def test_committed_golden_equals_build_from_script():
    script = json.loads(SCRIPT.read_text(encoding="utf-8-sig"))
    built = build_golden(script)
    committed = json.loads(BUILT_GOLDEN.read_text(encoding="utf-8-sig"))
    assert built == committed


def test_committed_synth_fixture_validates():
    script = json.loads(SCRIPT.read_text(encoding="utf-8-sig"))
    assert validate_golden(golden_from_data(build_golden(script))) is True


# ── 코드리뷰 회귀 (max-effort 리뷰 확정 결함) ─────────────────────────────

def _turn(text):
    return {"clip_id": "x", "turns": [
        {"segment_id": "s1", "speaker": "p1", "start_sec": 0, "end_sec": 1, "text": text}]}


def test_unbalanced_markup_raises():
    # ①: 닫는 대괄호 누락 → _MARKUP 미매치 → 마크업이 조용히 새면 안 됨(무성 실패 차단).
    with pytest.raises(ValueError):
        build_golden(_turn("예산은 [[3천만원|AMOUNT|budget_cap]으로 잡읍시다"))


def test_leaked_markup_among_valid_raises():
    # ①: 유효 마크업 뒤 오탈 마크업이 섞여도 잔여 '[['를 잡아 raise.
    with pytest.raises(ValueError):
        build_golden(_turn("예산 [[3천만원|AMOUNT]] 또 [[5천만원|AMOUNT] 끝"))


def test_markup_type_whitespace_tolerated():
    # ④: TYPE/key 앞뒤 공백은 strip해 정상 처리.
    g = build_golden(_turn("예산은 [[3천만원| AMOUNT | budget_cap ]]입니다"))
    e = g["segments"][0]["critical_entities"][0]
    assert e["type"] == "AMOUNT"
    assert e["canonical"]["value"] == 30_000_000
    assert e["contradiction_key"] == "budget_cap"


def test_too_many_markup_fields_raises():
    # ④: surface|TYPE|key 초과 필드는 무성 폐기 대신 raise.
    with pytest.raises(ValueError):
        build_golden(_turn("예산 [[3천만원|AMOUNT|budget_cap|extra]] 끝"))


def test_amount_without_currency_has_no_fabricated_unit():
    # ②: 통화어 없는 AMOUNT는 KRW 날조 금지 — 파서 그대로(unit 키 없음).
    g = build_golden(_turn("예산은 [[3천만|AMOUNT|budget_cap]] 정도예요"))
    e = g["segments"][0]["critical_entities"][0]
    assert e["canonical"] == {"value": 30_000_000}
    assert validate_golden(golden_from_data(g)) is True


def test_amount_with_currency_keeps_krw():
    # ② 회귀: 원이 있으면 KRW 유지.
    e = build_golden(_turn("[[3천만원|AMOUNT]]"))["segments"][0]["critical_entities"][0]
    assert e["canonical"] == {"value": 30_000_000, "unit": "KRW"}


def test_time_entity_carries_meridiem():
    # ③: fixture의 TIME 토큰은 오전/오후를 스팬에 포함해 meridiem을 실어야 한다.
    script = json.loads(SCRIPT.read_text(encoding="utf-8-sig"))
    g = build_golden(script)
    times = [e for seg in g["segments"] for e in seg["critical_entities"] if e["type"] == "TIME"]
    assert times and all("meridiem" in e["canonical"] for e in times)


def test_cli_writes_golden_and_manifest(tmp_path):
    # ⑥: CLI가 광고대로 골든 + TTS 매니페스트 둘 다 산출.
    out, man = tmp_path / "g.json", tmp_path / "m.json"
    rc = main(["--script", str(SCRIPT), "--out", str(out), "--manifest-out", str(man)])
    assert rc == 0 and out.exists() and man.exists()
    manifest = json.loads(man.read_text(encoding="utf-8"))
    assert len(manifest) == len(json.loads(out.read_text(encoding="utf-8"))["segments"])
    assert all("[[" not in t["text"] and "|" not in t["text"] for t in manifest)
    assert manifest[0]["speaker"] == "p1"


# ── 마크업 문법 확장: PROPER_NOUN aliases · flags.manual opt-out ───────────
# (채점기·검증기는 이미 aliases/flags.manual을 소비 — synth 마크업 방출만 확장)

def _ent0(text):
    return build_golden(_turn(text))["segments"][0]["critical_entities"][0]


def test_proper_noun_aliases_emitted_reach_scorer_model():
    # aliases=는 축약 허용목록을 골든에 싣는다 → golden_from_data가 채점기 데이터 모델
    # (CriticalEntity.aliases)로 그대로 전달, 검증 게이트 통과. (별칭 매칭 자체는 채점기 테스트 소관.)
    g = build_golden(_turn("이번에 새로 나온 [[루미|PROPER_NOUN|aliases=루미에,Lumi]]"))
    e = g["segments"][0]["critical_entities"][0]
    assert set(e["aliases"]) == {"루미에", "Lumi"}
    golden = golden_from_data(g)
    ent = golden["segments"][0].critical_entities[0]
    assert set(ent.aliases) == {"루미에", "Lumi"}
    assert validate_golden(golden) is True


def test_aliases_on_non_proper_noun_raises():
    # aliases는 PROPER_NOUN 채점 경로에서만 소비됨 → 다른 타입엔 무성 사장 방지 위해 에러.
    with pytest.raises(ValueError):
        build_golden(_turn("예산 [[3천만원|AMOUNT|aliases=삼천만]] 배정"))


def test_manual_flag_opts_out_of_parser_and_is_ambiguous():
    # parser가 못 다루는 정당 표기('정오')를 manual로 통과 — 검증 게이트 skip, 채점기 ambiguous.
    g = build_golden(_turn("점심은 [[정오|TIME|manual|canonical=정오]]에 먹읍시다"))
    e = g["segments"][0]["critical_entities"][0]
    assert e["flags"] == {"manual": True}
    assert e["canonical"]  # 비어있지 않음 (검증 게이트 요구)
    assert validate_golden(golden_from_data(g)) is True  # manual이라 parse 게이트 면제
    golden = golden_from_data(g)
    hyp = {"clip_id": "x", "provider": "m",
           "segments": {"s1": g["segments"][0]["text"]}}
    m = score_meeting(golden, hyp)
    assert m["per_type"]["TIME"].ambiguous == 1


def test_manual_without_explicit_canonical_defaults_to_surface():
    # manual인데 canonical= 생략 → surface를 canonical로. (비어있지 않아 게이트 통과)
    e = _ent0("점심은 [[정오|TIME|manual]]에")
    assert e["flags"] == {"manual": True}
    assert e["canonical"] == {"canonical": "정오"}


def test_canonical_without_manual_raises():
    # canonical= 는 manual 전용 — parser 파생 엔티티에 주면 게이트와 충돌하므로 에러.
    with pytest.raises(ValueError):
        build_golden(_turn("예산 [[3천만원|AMOUNT|canonical=30000000]] 배정"))


def test_unknown_named_field_raises():
    # 알 수 없는 명명 필드는 무성 폐기 대신 에러.
    with pytest.raises(ValueError):
        build_golden(_turn("신제품 [[루미|PROPER_NOUN|foo=bar]] 출시"))


def test_key_and_aliases_coexist():
    # contradiction_key(명명형)와 aliases 공존.
    e = _ent0("신제품 [[루미|PROPER_NOUN|key=brand|aliases=Lumi]] 출시")
    assert e["contradiction_key"] == "brand"
    assert set(e["aliases"]) == {"Lumi"}


def test_bare_third_field_still_means_contradiction_key():
    # 하위호환: surface|TYPE|key 의 맨 앞 무명 필드는 여전히 contradiction_key.
    e = _ent0("예산 [[3천만원|AMOUNT|budget_cap]] 배정")
    assert e["contradiction_key"] == "budget_cap"
    assert "aliases" not in e and "flags" not in e


# ── 리뷰 회귀 (적대적 리뷰 확정 결함 — 무성 no-op 차단) ────────────────────

def test_empty_aliases_value_raises():
    # [2]: aliases= 를 명시했는데 값이 비면 별칭 기능이 통째로 무력화 → 무성 대신 에러.
    with pytest.raises(ValueError):
        build_golden(_turn("신제품 [[루미|PROPER_NOUN|aliases=]] 출시"))


def test_comma_only_aliases_raises():
    # [2]: 콤마만 남은 값도 0개 별칭 → 에러.
    with pytest.raises(ValueError):
        build_golden(_turn("신제품 [[루미|PROPER_NOUN|aliases=,]] 출시"))


def test_empty_key_value_raises():
    # [2]: key= 값이 비면 contradiction_key가 조용히 사라짐 → 에러.
    with pytest.raises(ValueError):
        build_golden(_turn("예산 [[3천만원|AMOUNT|key=]] 배정"))


def test_aliases_with_manual_raises():
    # [4]: manual 엔티티는 채점기가 ambiguous로 단락 → aliases는 절대 소비 안 됨(죽은 별칭) → 에러.
    with pytest.raises(ValueError):
        build_golden(_turn("신제품 [[루미|PROPER_NOUN|manual|aliases=Lumi]] 출시"))


def test_duplicate_key_bare_and_named_raises():
    # [10/12]: 무명(첫 자리) key와 명명 key= 이중 지정을 last-wins로 삼키지 말고 에러.
    with pytest.raises(ValueError):
        build_golden(_turn("예산 [[3천만원|AMOUNT|budget_cap|key=other]] 배정"))


def test_empty_surface_markup_raises():
    # [8]: 빈 surface([[|TYPE]])는 무의미 토큰 — 게이트 구멍 대신 빌더가 즉시 에러.
    with pytest.raises(ValueError):
        build_golden(_turn("공백 [[|PROPER_NOUN]] 토큰"))

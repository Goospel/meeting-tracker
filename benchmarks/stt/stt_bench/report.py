"""회의 단위 채점 + 마크다운 리포트 렌더 + CLI.

PR1 최소판: 골든셋과 (모의) hypothesis를 받아 세그먼트별로 채점·병합하고,
'전체 CER은 참고치, CTER이 1순위'라는 이 제품의 핵심 논지를 리포트로 보인다.
통계 판정층(McNemar·부트스트랩)·화자귀속은 v2.

실행:
    python -m stt_bench.report --golden fixtures/golden/budget_meeting.json \
        --hyp fixtures/hyp/budget_meeting.aws_mock.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .golden import load_golden, load_hypothesis, validate_golden
from .score import TypeAgg, score_clip


def score_meeting(golden: dict, hyp: dict) -> dict:
    """세그먼트별 채점을 회의 단위로 병합."""
    per_type: dict[str, TypeAgg] = {}
    fcs, mts, seg_cer = [], [], {}
    hyp_segs = hyp["segments"]

    # clip_id 대조 (R13): 다른 회의의 hyp를 넘기면 즉시 실패.
    if golden.get("clip_id") and hyp.get("clip_id") and golden["clip_id"] != hyp["clip_id"]:
        raise ValueError(f"clip_id 불일치: golden {golden['clip_id']!r} vs hyp {hyp['clip_id']!r}")

    golden_ids = [seg.segment_id for seg in golden["segments"]]
    golden_id_set = set(golden_ids)
    total_failure = len(hyp_segs) == 0   # 빈 hyp = 완전 실패 (R14)

    # 조인 키 가드 (F6/R14): hyp에 세그먼트가 '있는데' 0개 일치일 때만 실패.
    # 빈 hyp는 정당한 완전 실패이므로 전부 삭제로 채점한다(크래시 금지).
    if hyp_segs and not any(sid in hyp_segs for sid in golden_ids):
        raise ValueError(
            f"hypothesis 세그먼트 id가 골든과 하나도 안 맞습니다 "
            f"(골든 {golden_ids} vs hyp {list(hyp_segs)}). 조인 키를 확인하세요."
        )
    uncovered = [sid for sid in golden_ids if sid not in hyp_segs]
    extra = [sid for sid in hyp_segs if sid not in golden_id_set]   # 환각 세그먼트 (R13)

    for seg in golden["segments"]:
        htext = hyp_segs.get(seg.segment_id, "")
        cs = score_clip(seg.text, seg.critical_entities, htext)
        seg_cer[seg.segment_id] = cs.cer
        for tname, agg in cs.per_type.items():
            m = per_type.setdefault(tname, TypeAgg())
            m.n += agg.n
            m.hit += agg.hit
            m.sub += agg.sub
            m.deleted += agg.deleted
            m.ambiguous += agg.ambiguous
        fcs.extend(cs.false_contradiction_candidates)
        mts.extend(cs.missed_token_candidates)

    return {
        "per_type": per_type,
        "false_contradiction_candidates": fcs,
        "missed_token_candidates": mts,
        "per_segment_cer": seg_cer,
        "uncovered_segments": uncovered,
        "extra_segments": extra,
        "total_failure": total_failure,
    }


def render_report(golden: dict, hyp: dict, merged: dict) -> str:
    L = []
    L.append(f"# STT 벤치마크 리포트 — {golden['clip_id']}")
    L.append("")
    L.append(f"- provider: **{hyp['provider']}**")
    L.append("- ⚠️ **데모/모의 데이터** — 실제 CLOVA/AWS API 호출이 아닙니다(합성 hypothesis). 실측 결과 아님.")
    if merged.get("total_failure"):
        L.append("- 🛑 **완전 실패** — hypothesis에 세그먼트가 하나도 없음. 전부 삭제로 채점(STT 완전 미출력).")
    if merged.get("uncovered_segments"):
        L.append(f"- ⚠️ hypothesis에 없는 골든 세그먼트 {merged['uncovered_segments']} — 전부 삭제로 채점됨(조인 키 확인).")
    if merged.get("extra_segments"):
        L.append(f"- ⚠️ **환각 세그먼트** {merged['extra_segments']} — 골든에 없는데 hyp에만 존재(모순감지 최고 위험군).")
    L.append("")
    L.append("## 엔티티 유형별 — CTER(치명 토큰 오류율)이 1순위 KPI")
    L.append("")
    L.append("| 유형 | n | hit | sub(치환) | del(삭제) | ambig | CTER | needs_review | sub_rate | del_rate |")
    L.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    total_ambig = 0
    for t, a in sorted(merged["per_type"].items()):
        total_ambig += a.ambiguous
        # 불변식: hit+sub+del+ambig == n
        assert a.hit + a.sub + a.deleted + a.ambiguous == a.n, f"{t} 집계 불변식 위반"
        L.append(
            f"| {t} | {a.n} | {a.hit} | {a.sub} | {a.deleted} | {a.ambiguous} | "
            f"{a.cter:.2f} | {a.needs_review_rate:.2f} | {a.sub_rate:.2f} | {a.del_rate:.2f} |"
        )
    if total_ambig:
        L.append("")
        L.append(f"> ⚠️ **needs_review {total_ambig}건** — 범위 붕괴·파서 밖 표기 등 판정 보류. CTER엔 미포함이나 무시 금지.")
    L.append("")

    fcs = merged["false_contradiction_candidates"]
    L.append(f"## 가짜 모순 후보 — 값 치환 {len(fcs)}건 *(그럴듯해서 grounding 통과하는 침묵형 지뢰)*")
    for c in fcs:
        L.append(f"- `{c.type}` {c.ref_value} → {c.hyp_value}  (hyp: “{c.hyp_surface}”)")
    L.append("")

    mts = merged["missed_token_candidates"]
    L.append(f"## 놓친 모순 후보 — 삭제 {len(mts)}건 *(한쪽 발언을 잃는 침묵형 FN)*")
    for c in mts:
        L.append(f"- `{c.type}` {c.ref_value} 소실  (hyp: “{c.hyp_surface}”)")
    L.append("")

    L.append("## 전체 CER — 참고치(1순위 아님)")
    for sid, cr in merged["per_segment_cer"].items():
        L.append(f"- {sid}: raw {cr.raw:.3f} · norm {cr.norm:.3f}" + (" · ⚠️outlier" if cr.outlier else ""))
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="STT 벤치마크 리포트 (meeting-tracker)")
    ap.add_argument("--golden", required=True, help="골든셋 JSON 경로")
    ap.add_argument("--hyp", required=True, help="hypothesis JSON 경로")
    ap.add_argument("--out", help="마크다운 출력 경로(생략 시 stdout)")
    a = ap.parse_args(argv)

    # Windows 콘솔 기본 인코딩(cp949)이 한글·em-dash를 못 찍는 무성 실패 회피.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    golden = load_golden(a.golden)
    validate_golden(golden)
    hyp = load_hypothesis(a.hyp)
    md = render_report(golden, hyp, score_meeting(golden, hyp))

    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

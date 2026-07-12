"""회의 단위 감지 채점 리포트(마크다운) + CLI.

골든 회의 + 예측 flag → per-type P/R/F1 표 + 가짜(FP)/놓친(FN)/타입혼동 목록.
순수·결정적. CLI는 골든을 먼저 검증(malformed 골든 조기 차단)한 뒤 채점한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .labels import FlagType, load_meeting, load_pred_flags, validate_golden
from .score import DetectionScore, score_detection

_TYPES = [t.value for t in FlagType]


def _safe(s) -> str:
    # 예측 flag id/type/인용은 신뢰 불가(외부 Claude 출력) — 마크다운(특히 표)을 깨지 않도록 무력화.
    # 개행(\n·\r 둘 다 CommonMark 줄바꿈)→공백, 백틱→작은따옴표, 파이프(표 열 구분자)→'/',
    # HTML 활성 문자(& < >)→엔티티(마크다운 뷰어의 인라인 HTML 렌더/주입 차단; & 먼저 — 이중 이스케이프 방지).
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\r", " ").replace("\n", " ")
            .replace("`", "'").replace("|", "/"))


def format_report(golden_meeting: dict, score: DetectionScore) -> str:
    meta = golden_meeting.get("meta", {})
    title = meta.get("title", "(제목 없음)")
    lines = [
        f"# 감지 품질 리포트 — {title}",
        "",
        f"- 전사 세그먼트: {len(golden_meeting['transcript'])}개 · 골든 flag: {len(golden_meeting['flags'])}개",
        f"- **종합(type-strict)**: 정밀도 {score.overall.precision:.2f} · 재현율 {score.overall.recall:.2f} · F1 {score.overall.f1:.2f}"
        f"  (TP {score.overall.tp} / FP {score.overall.fp} / FN {score.overall.fn})",
        f"- **localization(type-무관)**: 재현율 {score.localization.recall:.2f}"
        f"  — 흐름단절 자체는 찾았는지 (라벨 무시)",
        "",
        "## 유형별",
        "",
        "| 유형 | 골든 | TP | FP(가짜) | FN(놓침) | 정밀도 | 재현율 | F1 |",
        "|---|--:|--:|--:|--:|--:|--:|--:|",
    ]
    extra = [t for t in score.per_type if t not in _TYPES]   # 예측 미지 type(강등) 행
    for t in _TYPES + sorted(extra):
        prf = score.per_type[t]
        n = prf.tp + prf.fn
        lines.append(
            f"| {_safe(t)} | {n} | {prf.tp} | {prf.fp} | {prf.fn} | "
            f"{prf.precision:.2f} | {prf.recall:.2f} | {prf.f1:.2f} |"
        )
    lines.append("")

    lines.append("## 🔴 가짜 감지 (false positive — 없는 흐름단절을 지어냄)")
    if not score.false_positives:
        lines.append("- 없음")
    else:
        for fp in score.false_positives:
            if fp.reason == "ungrounded":
                why = "할루시 인용(전사에 없음)"
            elif fp.reason == "no_evidence":
                why = "근거 인용 없음(statements 비어있음)"
            elif fp.type_confused:
                why = "라벨 오분류(흐름단절은 잡음 — 아래 🔵 타입 혼동 참조)"
            else:
                why = "골든에 대응 없음"
            seg = f" @ {', '.join(fp.segments)}" if fp.segments else ""
            lines.append(f"- `{_safe(fp.flag_id)}` [{_safe(fp.type)}] — {why}{seg}")
    lines.append("")

    lines.append("## 🟡 놓친 감지 (miss — 실제 흐름단절을 못 잡음)")
    if not score.misses:
        lines.append("- 없음")
    else:
        for m in score.misses:
            tag = " (라벨만 틀림·localization은 잡음)" if m.type_confused else " (순수 놓침)"
            lines.append(f"- `{_safe(m.flag_id)}` [{_safe(m.type)}] @ {', '.join(m.segments)}{tag}")
    lines.append("")

    if score.type_confusions:
        lines.append("## 🔵 타입 혼동 (흐름단절은 찾았으나 라벨 오분류)")
        for tc in score.type_confusions:
            lines.append(
                f"- `{_safe(tc.golden_flag_id)}`(골든 {_safe(tc.golden_type)}) ↔ "
                f"`{_safe(tc.pred_flag_id)}`(예측 {_safe(tc.pred_type)}) @ {', '.join(tc.segments)}"
            )
        lines.append("")

    if score.tainted_matches:
        lines.append("## 🟠 정타 속 할루시 인용 (매칭은 됐지만 일부 인용이 전사에 없음)")
        for tm in score.tainted_matches:
            quotes = " · ".join(f"“{_safe(q)}”" for q in tm.ungrounded_quotes)
            lines.append(
                f"- `{_safe(tm.pred_flag_id)}` (골든 `{_safe(tm.golden_flag_id)}` 정타) — {quotes}"
            )
        lines.append("")

    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="감지 품질 리포트 (meeting-tracker 2단계)")
    ap.add_argument("--golden", required=True, help="골든 회의 JSON 경로")
    ap.add_argument("--pred", required=True, help="예측 flag JSON 경로 (리스트 또는 {flags:[...]})")
    ap.add_argument("--out", help="(선택) 마크다운 출력 경로 — 없으면 stdout")
    a = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):           # Windows cp949 콘솔 회피 (T-027)
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    try:
        golden = load_meeting(a.golden)
        validate_golden(golden)                       # malformed 골든 조기 차단
        pred = load_pred_flags(a.pred)
    except (ValueError, OSError, KeyError) as exc:     # KeyError = 필수 키 누락 malformed 입력
        print(f"채점 불가: {exc}", file=sys.stderr)
        return 2

    report = format_report(golden, score_detection(golden, pred))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(report + "\n", encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""합성 골든셋 빌더 (Track A) — '스크립트 하나 → 골든 + TTS 매니페스트'.

녹음 없이 벤치마크 데이터를 확보하는 2트랙 중 Track A. 회의를 인라인 마크업
스크립트로 **한 번만** 작성하면, 빌더가 같은 소스에서
  (1) CTER 골든 JSON — 문자 오프셋을 자동 계산.
  (2) TTS 렌더 매니페스트 — 마크업을 벗긴 화자별 발화.
를 파생한다 → 골든과 렌더 오디오가 어긋날 수 없다(무드리프트).

마크업 문법:
    [[surface|TYPE]]                     예) [[세 편|UNIT_QUANTITY]]
    [[surface|TYPE|contradiction_key]]   예) [[3천만원|AMOUNT|budget_cap]]

canonical은 저자가 쓰지 않고 parse_number/parse_date/parse_time으로 파생한다
— surface가 곧 정답 텍스트이므로 '값 등가 정답'은 그 파싱 결과여야 한다. 파싱
불가하거나 오탈·불균형 마크업은 즉시 에러(무성 실패 차단).

검증 주의: 오프셋 불변식(text[cs:ce]==surface)은 자동 계산이라 '구성상' 성립하고
validate_golden이 이를 실제로 검사한다. 반면 canonical은 파서 파생이라
validate_golden의 canonical 대조(_check_canonical)는 자명하게 통과할 뿐 — 파서
자체의 오파싱은 게이트가 못 잡는다(양쪽이 같은 파서). 그 방어는 회귀 테스트가 진다.

실행(골든 + 매니페스트 재생성):
    python -m stt_bench.synth --script fixtures/synth/budget_reversal.script.json \
        --out fixtures/golden/synth_budget_reversal.json \
        --manifest-out fixtures/synth/budget_reversal.manifest.json
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

from .korean_datetime import parse_date, parse_time
from .korean_numbers import currency_code, parse_number

_MARKUP = re.compile(r"\[\[(.+?)\]\]")

_NUMERIC_VALUE = {"AMOUNT", "NUMBER", "PERCENT", "UNIT_QUANTITY"}


def _strip_markup(raw: str) -> tuple[str, list[dict]]:
    """마크업 텍스트 → (마크업 제거·NFC 정규화한 clean 텍스트, 엔티티 스팬 목록).

    clean 텍스트 상의 문자 오프셋을 직접 계산해 채워 넣는다.
    """
    raw = unicodedata.normalize("NFC", raw)
    clean = ""
    spans: list[dict] = []
    idx = 0
    for m in _MARKUP.finditer(raw):
        clean += raw[idx:m.start()]
        parts = m.group(1).split("|")
        if len(parts) > 3:
            raise ValueError(f"마크업 필드 과다 (surface|TYPE|key만 지원): {m.group(1)!r}")
        surface = parts[0]
        etype = parts[1].strip() if len(parts) > 1 else ""   # TYPE 앞뒤 공백 허용
        key = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
        cs = len(clean)
        clean += surface
        spans.append({"surface": surface, "type": etype, "key": key,
                      "char_start": cs, "char_end": len(clean)})
        idx = m.end()
    clean += raw[idx:]
    # 오탈·불균형 마크업이 조용히 새는 것 차단 — 무성 실패는 이 빌더의 최악 실패모드.
    if "[[" in clean or "]]" in clean:
        raise ValueError(f"불균형/오탈 마크업이 남았습니다 (닫는 ']]' 확인): {raw!r}")
    return clean, spans


def _derive_canonical(etype: str, surface: str) -> dict:
    """surface를 파싱해 canonical을 만든다 — validate_golden의 _check_canonical과
    같은 규칙이라 산출 골든은 게이트를 항상 통과한다."""
    if etype == "DATE":
        p = parse_date(surface)
        if not p:
            raise ValueError(f"DATE surface {surface!r} 파싱 실패")
        return p
    if etype == "TIME":
        p = parse_time(surface)
        if not p:
            raise ValueError(f"TIME surface {surface!r} 파싱 실패")
        return p
    if etype == "RANGE":
        r = parse_number(surface)
        if r.kind != "range":
            raise ValueError(f"RANGE surface {surface!r} 범위 파싱 실패 ({r.kind})")
        return {"low": r.low, "high": r.high}
    if etype in _NUMERIC_VALUE:
        r = parse_number(surface)
        if r.kind != "value":
            raise ValueError(f"{etype} surface {surface!r} 값 파싱 실패 ({r.kind})")
        if etype == "AMOUNT":
            cur = currency_code(r.unit)   # 통화어 없으면 unit 생략 — KRW 날조 금지
            return {"value": r.value, "unit": cur} if cur else {"value": r.value}
        if etype == "UNIT_QUANTITY":
            return {"value": r.value, "unit": r.unit}
        return {"value": r.value}
    if etype == "PROPER_NOUN":
        return {"canonical": surface}
    raise ValueError(f"지원하지 않는 엔티티 유형: {etype!r}")


def build_golden(script: dict) -> dict:
    """마크업 스크립트 → 골든 raw dict(fixtures/golden 스키마)."""
    segments = []
    for turn in script["turns"]:
        text, spans = _strip_markup(turn["text"])
        ents = []
        for i, sp in enumerate(spans, start=1):
            e = {
                "entity_id": f"{turn['segment_id']}e{i}",
                "type": sp["type"],
                "char_start": sp["char_start"],
                "char_end": sp["char_end"],
                "surface": sp["surface"],
                "canonical": _derive_canonical(sp["type"], sp["surface"]),
                "criticality": "high",
            }
            if sp["key"]:
                e["contradiction_key"] = sp["key"]
            ents.append(e)
        segments.append({
            "segment_id": turn["segment_id"],
            "speaker": turn["speaker"],
            "start_sec": turn["start_sec"],
            "end_sec": turn["end_sec"],
            "text": text,
            "critical_entities": ents,
        })
    return {
        "schema_version": "1.0-nfc",
        "clip_id": script["clip_id"],
        "audio": {
            "source_type": "tts_synthetic",
            "note": script.get(
                "note",
                "합성 데이터 — 실제 API 아님. 스크립트에서 골든+TTS 매니페스트를 파생.",
            ),
        },
        "speakers": script.get("speakers", []),
        "segments": segments,
    }


def render_manifest(script: dict) -> list[dict]:
    """마크업 스크립트 → TTS 렌더용 화자별 발화(마크업 제거). 골든과 같은 소스."""
    manifest = []
    for turn in script["turns"]:
        text, _ = _strip_markup(turn["text"])
        manifest.append({
            "segment_id": turn["segment_id"],
            "speaker": turn["speaker"],
            "start_sec": turn["start_sec"],
            "end_sec": turn["end_sec"],
            "text": text,
        })
    return manifest


def main(argv=None) -> int:
    import json

    from .golden import golden_from_data, validate_golden

    ap = argparse.ArgumentParser(description="합성 골든셋 빌더 (Track A)")
    ap.add_argument("--script", required=True, help="마크업 스크립트 JSON 경로")
    ap.add_argument("--out", required=True, help="골든 JSON 출력 경로")
    ap.add_argument("--manifest-out", help="(선택) TTS 렌더 매니페스트 JSON 출력 경로")
    a = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows cp949 콘솔 회피 (T-027)
    except (AttributeError, ValueError):
        pass

    script = json.loads(Path(a.script).read_text(encoding="utf-8-sig"))
    golden = build_golden(script)
    validate_golden(golden_from_data(golden))  # 오프셋 불변식 검증
    Path(a.out).write_text(
        json.dumps(golden, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    n = sum(len(s["critical_entities"]) for s in golden["segments"])
    print(f"wrote {a.out} — 세그먼트 {len(golden['segments'])}개, 치명토큰 {n}개")

    if a.manifest_out:
        manifest = render_manifest(script)
        Path(a.manifest_out).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {a.manifest_out} — TTS 매니페스트 발화 {len(manifest)}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

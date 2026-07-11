"""골든셋 로더 + 검증 게이트 테스트."""

import json
from pathlib import Path

import pytest

from stt_bench.golden import load_golden, load_hypothesis, validate_golden

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def test_load_golden_parses_entities():
    g = load_golden(FIX / "golden" / "budget_meeting.json")
    assert g["clip_id"] == "budget_meeting_demo"
    segs = g["segments"]
    assert len(segs) == 2
    e = segs[0].critical_entities[0]
    assert e.type.value == "AMOUNT"
    assert e.canonical["value"] == 30_000_000
    assert e.contradiction_key == "budget_cap"


def test_validate_passes_on_good_offsets():
    g = load_golden(FIX / "golden" / "budget_meeting.json")
    assert validate_golden(g) is True


def test_load_hypothesis():
    h = load_hypothesis(FIX / "hyp" / "budget_meeting.aws_mock.json")
    assert h["provider"] == "aws_mock"
    assert h["segments"]["s1"].startswith("예산은 2천만원")


def test_validate_rejects_offset_surface_mismatch(tmp_path):
    # char_start:char_end 가 surface 와 불일치 → 커밋 게이트가 막아야 한다.
    bad = {
        "clip_id": "x",
        "segments": [
            {
                "segment_id": "s",
                "speaker": "p1",
                "start_sec": 0,
                "end_sec": 1,
                "text": "예산 3천만원",
                "critical_entities": [
                    {
                        "entity_id": "e",
                        "type": "AMOUNT",
                        "char_start": 0,
                        "char_end": 2,
                        "surface": "3천만원",
                        "canonical": {"value": 30000000},
                    }
                ],
            }
        ],
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_golden(load_golden(p))

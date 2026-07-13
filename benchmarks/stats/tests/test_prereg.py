"""사전등록 동결 — sorted-key JSON + 콘텐츠 해시로 사후 손잡이 조정을 폭로."""

import pytest

from bench_stats.prereg import dump_prereg, freeze_prereg, load_prereg, prereg_hash


def _cfg():
    return freeze_prereg(
        primary_endpoint="recall",
        estimand="meeting_weighted",
        alpha=0.05,
        target=0.85,
        test="cluster_sign_permutation",
        inference_floor=6,
    )


def test_freeze_deterministic_hash():
    assert prereg_hash(_cfg()) == prereg_hash(_cfg())


def test_field_change_changes_hash():
    a = _cfg()
    b = freeze_prereg(
        primary_endpoint="recall", estimand="flag_weighted",  # 변경
        alpha=0.05, target=0.85, test="cluster_sign_permutation", inference_floor=6)
    assert prereg_hash(a) != prereg_hash(b)


def test_dump_load_roundtrip(tmp_path):
    cfg = _cfg()
    p = tmp_path / "prereg.json"
    dump_prereg(cfg, p)
    loaded = load_prereg(p)
    assert prereg_hash(loaded) == prereg_hash(cfg)
    assert loaded.data == cfg.data


def test_dump_byte_reproducible(tmp_path):
    p1, p2 = tmp_path / "a.json", tmp_path / "b.json"
    dump_prereg(_cfg(), p1)
    dump_prereg(_cfg(), p2)
    assert p1.read_bytes() == p2.read_bytes()      # sorted-key → 바이트 재현


def test_load_detects_tamper(tmp_path):
    p = tmp_path / "prereg.json"
    dump_prereg(_cfg(), p)
    raw = p.read_text(encoding="utf-8").replace("0.85", "0.70")  # 손잡이 조정
    p.write_text(raw, encoding="utf-8", newline="")
    with pytest.raises(ValueError):
        load_prereg(p)                              # 해시 불일치 → fail-loud

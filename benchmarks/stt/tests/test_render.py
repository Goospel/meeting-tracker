"""렌더 레이어 (Track A) — TtsPort + 크레덴셜-불요 톤 렌더러 + 팩토리 확장점.

런타임 의존성 0 불변식이라 Azure/Google SDK는 코어에 싣지 않는다. 대신 stdlib만으로
화자별 사인 톤을 산출하는 ToneTtsPort로 '매니페스트 → WAV 타임라인 + 렌더 리포트'
파이프라인을 실제로 검증한다. 크레덴셜이 오면 포트만 스왑한다(get_port 확장점).
"""

import json
import wave
from array import array
from pathlib import Path

import pytest

from stt_bench.render import (
    AudioClip,
    ToneTtsPort,
    TtsCredentialError,
    get_port,
    main,
    render_clip,
    write_wav,
)
from stt_bench.synth import render_manifest

FIX = Path(__file__).resolve().parent.parent / "fixtures"
SCRIPT = FIX / "synth" / "budget_reversal.script.json"


def _manifest():
    return [
        {"segment_id": "s1", "speaker": "p1", "start_sec": 0.0, "end_sec": 3.0, "text": "예산은 삼천만원입니다"},
        {"segment_id": "s2", "speaker": "p2", "start_sec": 3.0, "end_sec": 6.0, "text": "출시는 팔월입니다"},
        {"segment_id": "s3", "speaker": "p1", "start_sec": 6.0, "end_sec": 9.0, "text": "좋습니다"},
    ]


# ── ToneTtsPort: stdlib만으로 실제 PCM 산출 ───────────────────────────────

def test_tone_port_produces_nonsilent_clip():
    clip = ToneTtsPort().synthesize("안녕하세요 회의 시작합니다", speaker="p1")
    assert isinstance(clip, AudioClip)
    assert clip.duration_sec > 0
    assert any(s != 0 for s in clip.samples)          # 톤이 실려 있다(무음 아님)


def test_tone_port_deterministic_same_speaker():
    port = ToneTtsPort()
    a = port.synthesize("동일 발화", speaker="p1")
    b = port.synthesize("동일 발화", speaker="p1")
    assert list(a.samples) == list(b.samples)         # hash 비의존 결정성 (재현성)


def test_tone_port_distinguishes_speakers():
    port = ToneTtsPort()
    a = port.synthesize("같은 텍스트", speaker="A")     # ord 65 → 팔레트 0
    b = port.synthesize("같은 텍스트", speaker="B")     # ord 66 → 팔레트 1
    assert list(a.samples) != list(b.samples)


def test_tone_port_duration_grows_with_text():
    port = ToneTtsPort()
    short = port.synthesize("네", speaker="p1")
    long = port.synthesize("이것은 훨씬 더 긴 발화입니다 그래서 오디오도 더 깁니다", speaker="p1")
    assert long.duration_sec > short.duration_sec


# ── render_clip: 매니페스트 → 타임라인 + 렌더 리포트 ───────────────────────

def test_render_clip_concatenates_with_gaps_and_reports_positions():
    man = _manifest()
    clip, report = render_clip(man, ToneTtsPort(), gap_sec=0.3)
    assert len(report) == 3
    assert [r["segment_id"] for r in report] == ["s1", "s2", "s3"]
    assert [r["text"] for r in report] == [m["text"] for m in man]
    assert report[0]["start_sec"] == 0.0              # 첫 세그는 0에서 시작(선행 무음 없음)
    for prev, nxt in zip(report, report[1:]):
        assert nxt["start_sec"] >= prev["end_sec"]    # 세그먼트 사이 무음 gap → 비겹침·단조
    assert clip.duration_sec >= report[-1]["end_sec"] - 0.01


def test_render_empty_manifest_raises():
    with pytest.raises(ValueError):
        render_clip([], ToneTtsPort())


def test_write_wav_roundtrip(tmp_path):
    clip, _ = render_clip(_manifest(), ToneTtsPort())
    out = tmp_path / "clip.wav"
    write_wav(clip, out)
    with wave.open(str(out), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == clip.sample_rate
        assert w.getnframes() == len(clip.samples)


# ── get_port 팩토리: tone은 실동작 · azure/google은 명확히 막힘 · naver 금지 ──

def test_get_port_tone_is_working_renderer():
    clip = get_port("tone").synthesize("테스트", speaker="p1")
    assert clip.duration_sec > 0


def test_get_port_azure_and_google_raise_credential_error():
    # SDK·크레덴셜 확장점 — 코어엔 어댑터가 없으니 명확한 에러로 막는다(무성 실패 금지).
    for name in ("azure", "google"):
        with pytest.raises(TtsCredentialError):
            get_port(name)


def test_get_port_naver_rejected():
    # 벤치 대상에 클로바 포함 → 네이버 렌더는 벤더 편향으로 애초에 미지원.
    with pytest.raises(ValueError):
        get_port("naver")


# ── CLI: 스크립트 → 매니페스트 → 렌더 WAV + 리포트 (크레덴셜 없이 관통) ─────

def test_cli_renders_wav_and_report_from_manifest(tmp_path):
    script = json.loads(SCRIPT.read_text(encoding="utf-8-sig"))
    man = render_manifest(script)
    man_path = tmp_path / "m.json"
    man_path.write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")

    wav, rep = tmp_path / "clip.wav", tmp_path / "report.json"
    rc = main(["--manifest", str(man_path), "--out", str(wav), "--report-out", str(rep)])
    assert rc == 0 and wav.exists() and rep.exists()
    with wave.open(str(wav), "rb") as w:
        assert w.getnframes() > 0
    report = json.loads(rep.read_text(encoding="utf-8"))
    assert len(report) == len(man)
    assert report[0]["segment_id"] == "s1"


# ── 리뷰 회귀 (적대적 리뷰 확정 결함 — 크래시·무성 실패 차단) ──────────────

class _ZeroRatePort:
    """확장점 오용 시뮬레이션 — sample_rate=0을 반환하는 포트."""

    def synthesize(self, text, *, speaker):
        return AudioClip(array("h", [0, 0, 0]), 0)


def test_tone_port_rejects_out_of_range_amplitude():
    # [1]: amplitude>1은 int16 포화로 OverflowError 크래시 → 생성자에서 명확한 에러.
    for bad in (1.5, 0.0, -0.1):
        with pytest.raises(ValueError):
            ToneTtsPort(amplitude=bad)


def test_tone_port_rejects_nonpositive_rates():
    # [1]/[6]: sample_rate·sec_per_char 0/음수는 무의미 → 에러.
    with pytest.raises(ValueError):
        ToneTtsPort(sample_rate=0)
    with pytest.raises(ValueError):
        ToneTtsPort(sec_per_char=0)


def test_render_clip_rejects_negative_gap():
    # [5]: gap_sec 음수는 'negative count'라는 모호한 에러 대신 명확히 거부.
    with pytest.raises(ValueError):
        render_clip(_manifest(), ToneTtsPort(), gap_sec=-0.5)


def test_render_clip_rejects_non_list_manifest():
    # [16]: dict 등 비-list 매니페스트는 불명확한 TypeError 대신 도메인 에러.
    with pytest.raises(ValueError):
        render_clip({"s1": {"text": "x", "speaker": "p1", "segment_id": "s1"}}, ToneTtsPort())


def test_render_clip_rejects_zero_sample_rate_port():
    # [6]: 포트가 sample_rate=0을 반환하면 ZeroDivisionError 대신 명확한 에러.
    with pytest.raises(ValueError):
        render_clip(_manifest(), _ZeroRatePort())


def test_cli_creates_report_parent_dir(tmp_path):
    # [3]: --report-out 부모 디렉터리가 없어도 WAV만 쓰고 크래시하지 말고 리포트까지 산출.
    man = _manifest()
    man_path = tmp_path / "m.json"
    man_path.write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    wav = tmp_path / "out" / "clip.wav"
    rep = tmp_path / "nope" / "nested" / "report.json"
    rc = main(["--manifest", str(man_path), "--out", str(wav), "--report-out", str(rep)])
    assert rc == 0 and wav.exists() and rep.exists()


def test_cli_empty_manifest_returns_clean_error(tmp_path):
    # [9]: 빈 매니페스트는 미포착 트레이스백이 아니라 클린 에러(return 2).
    man_path = tmp_path / "m.json"
    man_path.write_text("[]", encoding="utf-8")
    rc = main(["--manifest", str(man_path), "--out", str(tmp_path / "clip.wav")])
    assert rc == 2

"""렌더 레이어 (Track A) — TTS 매니페스트 → 오디오 타임라인.

Track A는 '스크립트 하나 → 골든 + TTS 매니페스트'로 골든↔대본의 무드리프트를 보장한다
(synth.py). 이 모듈은 그 매니페스트를 실제 오디오로 렌더한다.

**설계 제약**: 이 벤치마크 코어는 런타임 의존성 0이 불변식이다(README). 실제 비-네이버
뉴럴 TTS(Azure Neural / Google Chirp3 HD)는 벤더 SDK가 필요하므로 이 코어에 싣지 않고,
`get_port`가 명확한 에러로 막는 **확장점**으로 둔다. 대신 stdlib만으로 화자별 사인 톤을
산출하는 `ToneTtsPort`를 실동작 렌더러로 제공한다 — 목적은 음향 사실성이 아니라
'매니페스트 → WAV 타임라인 + 렌더 리포트' **파이프라인의 실제 검증**이다. 크레덴셜이
확보되면 `TtsPort`를 만족하는 어댑터(선택 extra)를 붙여 포트만 스왑한다.

⚠️ 벤치 대상에 클로바가 포함되므로 실제 렌더러는 **반드시 비-네이버**여야 한다(같은 벤더
음향 prior로 인한 편향 회피) — `get_port("naver")`는 애초에 거부한다.

실행(매니페스트 → 타임라인 WAV + 렌더 리포트):
    python -m stt_bench.render \
        --manifest   fixtures/synth/budget_reversal.manifest.json \
        --out        fixtures/audio/synth_budget_reversal.wav \
        --report-out fixtures/synth/budget_reversal.render.json
"""

from __future__ import annotations

import argparse
import math
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

# 화자 구분용 사인 톤 팔레트(Hz). 음향 사실성이 아니라 파이프라인 검증이 목적이라
# 몇 개의 뚜렷한 음정이면 충분하다. 서로소 길이라 인접 화자(p1/p2/p3)는 다른 음정이 된다.
_PALETTE_HZ = (196.0, 233.08, 277.18, 329.63, 392.0)
_MIN_SEC = 0.3


class TtsCredentialError(RuntimeError):
    """실제 TTS 렌더에 필요한 크레덴셜/어댑터가 없을 때."""


@dataclass
class AudioClip:
    """모노 16-bit PCM 오디오. samples는 signed short(array 'h')."""

    samples: "array"
    sample_rate: int

    @property
    def duration_sec(self) -> float:
        return len(self.samples) / self.sample_rate if self.sample_rate else 0.0


@runtime_checkable
class TtsPort(Protocol):
    """발화 텍스트 → AudioClip. 실제 어댑터(Azure/Google)도 이 시그니처만 만족하면 된다."""

    def synthesize(self, text: str, *, speaker: str) -> AudioClip: ...


class ToneTtsPort:
    """크레덴셜-불요 렌더러 — 발화를 화자별 사인 톤으로 대체한다(stdlib만).

    지속시간은 텍스트 길이에서 유도해(자연 TTS 흉내) 실제 렌더러의 '대본과 다른 길이'
    특성을 흉내낸다. 화자→음정은 hash()가 아니라 코드포인트 합으로 결정(재현성 — 리뷰
    R: hash() 비결정성 회피). 서로 다른 화자가 같은 음정으로 충돌할 수 있으나 파이프라인
    검증에는 무해하다.
    """

    def __init__(self, sample_rate: int = 16_000, sec_per_char: float = 0.12,
                 amplitude: float = 0.3):
        # amplitude>1은 int16 포화로 OverflowError를 낸다 — 오사용을 크래시 대신 명확한 에러로.
        if not 0 < amplitude <= 1:
            raise ValueError(f"amplitude는 (0, 1] 범위여야 합니다: {amplitude}")
        if sample_rate <= 0:
            raise ValueError(f"sample_rate는 양수여야 합니다: {sample_rate}")
        if sec_per_char <= 0:
            raise ValueError(f"sec_per_char는 양수여야 합니다: {sec_per_char}")
        self.sample_rate = sample_rate
        self.sec_per_char = sec_per_char
        self.amplitude = amplitude

    def _freq(self, speaker: str) -> float:
        return _PALETTE_HZ[sum(map(ord, speaker)) % len(_PALETTE_HZ)]

    def synthesize(self, text: str, *, speaker: str) -> AudioClip:
        freq = self._freq(speaker)
        duration = max(_MIN_SEC, len(text) * self.sec_per_char)
        n = int(duration * self.sample_rate)
        peak = int(self.amplitude * 32767)
        step = 2 * math.pi * freq / self.sample_rate
        samples = array("h", (int(peak * math.sin(step * i)) for i in range(n)))
        return AudioClip(samples, self.sample_rate)


def render_clip(manifest: list[dict], port: TtsPort, *, gap_sec: float = 0.35):
    """매니페스트(화자별 발화) → (타임라인 AudioClip, 렌더 리포트).

    각 발화를 포트로 렌더해 순서대로 이어 붙이되 사이에 gap_sec 무음을 둔다. 리포트는
    각 세그먼트의 **실제 렌더 시각**(start/end, 초)을 담는다 — 실제 TTS는 대본 timing과
    길이가 다르므로, STT 출력 정렬·재생 링크는 authored timing이 아니라 이 리포트를 쓴다.
    """
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("빈 매니페스트 — 렌더할 발화가 없습니다 (list 형식이어야 함)")
    if gap_sec < 0:
        raise ValueError(f"gap_sec은 음수일 수 없습니다: {gap_sec}")

    samples = array("h")
    sample_rate: int | None = None
    gap_frames = 0
    report: list[dict] = []

    for i, seg in enumerate(manifest):
        clip = port.synthesize(seg["text"], speaker=seg["speaker"])
        if not clip.sample_rate:                      # 확장점(커스텀 어댑터) 오용 — ZeroDivision 대신 명확한 에러
            raise ValueError("포트가 sample_rate=0을 반환했습니다")
        if sample_rate is None:
            sample_rate = clip.sample_rate
            gap_frames = int(gap_sec * sample_rate)
        elif clip.sample_rate != sample_rate:
            raise ValueError(
                f"포트가 세그먼트마다 다른 sample_rate 반환: {sample_rate} != {clip.sample_rate}"
            )
        if i > 0 and gap_frames:
            samples.extend(array("h", bytes(2 * gap_frames)))   # 세그먼트 사이 무음
        start = len(samples) / sample_rate
        samples.extend(clip.samples)
        end = len(samples) / sample_rate
        report.append({
            "segment_id": seg["segment_id"],
            "speaker": seg["speaker"],
            "text": seg["text"],
            "start_sec": round(start, 3),
            "end_sec": round(end, 3),
        })

    return AudioClip(samples, sample_rate), report


def write_wav(clip: AudioClip, path: str | Path) -> None:
    """AudioClip → 모노 16-bit PCM WAV(리틀엔디언)."""
    data = clip.samples
    if sys.byteorder == "big":                 # WAV는 리틀엔디언 — 빅엔디언 머신이면 스왑
        data = array("h", clip.samples)
        data.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(clip.sample_rate)
        w.writeframes(data.tobytes())


_CRED_ENV = {"azure": "AZURE_SPEECH_KEY", "google": "GOOGLE_APPLICATION_CREDENTIALS"}


def get_port(name: str = "tone", **kwargs) -> TtsPort:
    """렌더러 팩토리. tone은 즉시 동작, azure/google은 크레덴셜·어댑터 확장점,
    naver는 벤더 편향으로 거부."""
    if name == "tone":
        return ToneTtsPort(**kwargs)
    if name in _CRED_ENV:
        raise TtsCredentialError(
            f"{name} 렌더는 크레덴셜과 벤더 SDK가 필요합니다. 런타임 의존성 0 불변식이라 "
            f"SDK는 이 코어에 싣지 않습니다 — {_CRED_ENV[name]}를 설정하고 TtsPort를 만족하는 "
            f"어댑터(선택 extra)를 붙여 포트를 스왑하세요. 벤치 대상에 클로바가 있으니 렌더는 "
            f"반드시 비-네이버(Azure/Google)."
        )
    raise ValueError(
        f"알 수 없는 렌더러: {name!r} (tone|azure|google). "
        f"네이버 계열은 벤더 음향 prior 편향으로 미지원."
    )


def main(argv=None) -> int:
    import json

    ap = argparse.ArgumentParser(description="TTS 매니페스트 렌더러 (Track A)")
    ap.add_argument("--manifest", required=True, help="TTS 렌더 매니페스트 JSON 경로")
    ap.add_argument("--out", required=True, help="타임라인 WAV 출력 경로")
    ap.add_argument("--report-out", help="(선택) 렌더 리포트 JSON 출력 경로")
    ap.add_argument("--renderer", default="tone", help="tone|azure|google (기본 tone)")
    ap.add_argument("--gap-sec", type=float, default=0.35, help="세그먼트 사이 무음(초)")
    a = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):           # Windows cp949 콘솔 회피 (T-027)
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8-sig"))
    try:
        # azure/google/naver 거부, 빈/비-list 매니페스트, gap 음수 등을 클린 에러로(트레이스백 대신).
        port = get_port(a.renderer)
        clip, report = render_clip(manifest, port, gap_sec=a.gap_sec)
    except (TtsCredentialError, ValueError) as exc:
        print(f"렌더 불가: {exc}", file=sys.stderr)
        return 2

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    write_wav(clip, a.out)
    print(f"wrote {a.out} — {clip.duration_sec:.1f}s, 세그먼트 {len(report)}개, 렌더러 {a.renderer}")

    if a.report_out:
        Path(a.report_out).parent.mkdir(parents=True, exist_ok=True)   # --out과 대칭 — 부분 산출 크래시 차단
        Path(a.report_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {a.report_out} — 렌더 리포트(실제 렌더 시각)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

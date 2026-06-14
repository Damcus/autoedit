"""Orchestrates the whole job and reports staged progress."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable, Optional

from . import ffmpeg_utils as ff
from .config import Settings, default_output_path
from .reframe import Crop, compute_crop
from .subtitles import build_ass
from .transcribe import transcribe

# (label, start%, end%) - lets the GUI show a single smooth 0..100 bar.
_STAGES = {
    "probe":      (0.00, 0.03),
    "audio":      (0.03, 0.08),
    "transcribe": (0.08, 0.62),
    "faces":      (0.62, 0.70),
    "subs":       (0.70, 0.73),
    "render":     (0.73, 1.00),
}


class Progress:
    def __init__(self, cb: Optional[Callable[[float, str], None]]):
        self._cb = cb

    def stage(self, name: str, frac: float = 0.0, msg: str = "") -> None:
        if not self._cb:
            return
        lo, hi = _STAGES[name]
        self._cb(lo + (hi - lo) * max(0.0, min(1.0, frac)), msg)


def _video_filter(s: Settings, crop: Optional[Crop], use_subs: bool) -> str:
    w, h = s.canvas()
    chain: list[str] = []

    if s.reframe == "blur":
        # Contain the clip on a blurred, zoomed copy of itself.
        flt = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},gblur=sigma=22[bgb];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
        if use_subs:
            flt += ",ass=subs.ass"
        return flt + "[v]"

    if s.reframe == "pad":
        chain.append(
            f"scale={w}:{h}:force_original_aspect_ratio=decrease")
        chain.append(
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    else:  # smartcrop
        if crop is not None:
            chain.append(f"crop={crop.w}:{crop.h}:{crop.x}:{crop.y}")
        chain.append(f"scale={w}:{h}:flags=lanczos")

    chain.append("setsar=1")
    if use_subs:
        chain.append("ass=subs.ass")
    return f"[0:v]{','.join(chain)}[v]"


def _audio_filter(s: Settings) -> str:
    return (
        "highpass=f=70,"
        "acompressor=threshold=-18dB:ratio=3:attack=20:release=250,"
        f"loudnorm=I={s.loudness_i}:TP={s.loudness_tp}:LRA={s.loudness_lra}"
    )


def process(
    src: str,
    settings: Settings,
    output: Optional[str] = None,
    log: Callable[[str], None] = print,
    progress: Optional[Callable[[float, str], None]] = None,
) -> str:
    """Run the full edit. Returns the path to the finished file."""
    settings.validate()
    ff.ensure_tools()

    src_path = Path(src)
    if not src_path.exists():
        raise FileNotFoundError(f"Input file not found: {src}")
    out_path = Path(output) if output else default_output_path(src_path)
    if out_path.resolve() == src_path.resolve():
        raise ValueError("Output path must differ from the input file.")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pr = Progress(progress)
    workdir = Path(tempfile.mkdtemp(prefix="autoedit_"))

    try:
        # 1. Probe -------------------------------------------------------
        pr.stage("probe", 0.0, "Reading the clip...")
        log(f"Opening: {src_path.name}")
        info = ff.probe(src_path)
        log(f"Source: {info.width}x{info.height}, "
            f"{info.duration:.1f}s, {info.fps:.0f} fps, "
            f"audio: {'yes' if info.has_audio else 'no'}")
        pr.stage("probe", 1.0)

        # Work out the trimmed window. eff_duration drives transcription and
        # render progress; trim_dur (or None) is what we pass to ffmpeg's -t.
        full = info.duration
        start = max(0.0, settings.clip_start)
        if full > 0 and start >= full:
            raise ValueError(
                f"Start time ({start:.1f}s) is at or past the end of the "
                f"clip ({full:.1f}s).")

        if settings.clip_duration and settings.clip_duration > 0:
            remaining = (full - start) if full > 0 else settings.clip_duration
            eff_duration = min(settings.clip_duration, remaining)
            trim_dur: float | None = eff_duration
        else:
            eff_duration = (full - start) if full > 0 else 0.0
            trim_dur = None

        if start > 0 or trim_dur is not None:
            shown = f"{eff_duration:.1f}s" if eff_duration > 0 else "to end"
            log(f"Trimming: start at {start:.1f}s, keep {shown}")

        want_subs = settings.caption_style != "none" and info.has_audio
        words = []

        if want_subs:
            # 2. Extract audio ------------------------------------------
            pr.stage("audio", 0.0, "Extracting audio...")
            wav = workdir / "audio.wav"
            ff.extract_audio(src_path, wav, start=start, duration=trim_dur)
            pr.stage("audio", 1.0)

            # 3. Transcribe ---------------------------------------------
            pr.stage("transcribe", 0.0, "Transcribing speech...")
            words = transcribe(
                str(wav),
                model_size=settings.model,
                language=settings.language,
                duration=eff_duration,
                log=log,
                on_progress=lambda f: pr.stage(
                    "transcribe", f, "Transcribing speech..."),
            )
        elif settings.caption_style != "none":
            log("No audio track - skipping captions.")

        # 4. Face-aware reframe ----------------------------------------
        crop: Optional[Crop] = None
        if settings.reframe == "smartcrop":
            pr.stage("faces", 0.0, "Finding the speaker...")
            cw, ch = settings.canvas()
            crop = compute_crop(str(src_path), cw, ch, log=log,
                                start=start, duration=trim_dur)
            pr.stage("faces", 1.0)

        # 5. Subtitles --------------------------------------------------
        use_subs = bool(words) and settings.caption_style != "none"
        if use_subs:
            pr.stage("subs", 0.0, "Building captions...")
            build_ass(words, settings, str(workdir / "subs.ass"))
            pr.stage("subs", 1.0)

        # 6. Render -----------------------------------------------------
        pr.stage("render", 0.0, "Rendering final video...")
        vf = _video_filter(settings, crop, use_subs)

        # -ss before -i is a fast (keyframe) seek; re-encoding resets the
        # output to start at 0, so the captions (built from 0) stay aligned.
        args = []
        if start > 0:
            args += ["-ss", f"{start:.3f}"]
        args += ["-i", str(src_path.resolve())]
        if trim_dur is not None:
            args += ["-t", f"{trim_dur:.3f}"]
        args += ["-filter_complex", vf, "-map", "[v]"]

        if info.has_audio:
            # -ar 48000: one-pass loudnorm upsamples to 192 kHz internally;
            # without it the AAC track ends up at an oversized sample rate.
            args += ["-map", "0:a:0", "-af", _audio_filter(settings),
                     "-c:a", "aac", "-b:a", settings.audio_bitrate,
                     "-ar", "48000"]

        args += [
            "-c:v", "libx264", "-preset", settings.preset,
            "-crf", str(settings.crf), "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(out_path.resolve()),
        ]

        ff.run_with_progress(
            args, eff_duration or info.duration,
            on_progress=lambda f: pr.stage("render", f,
                                           "Rendering final video..."),
            cwd=workdir,
        )

        log(f"Done -> {out_path}")
        if progress:
            progress(1.0, "Finished")
        return str(out_path)

    finally:
        shutil.rmtree(workdir, ignore_errors=True)

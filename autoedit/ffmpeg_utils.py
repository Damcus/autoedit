"""Thin wrappers around the system ffmpeg / ffprobe binaries."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Hide the console windows that subprocess would otherwise pop on Windows
# when the app is launched from the GUI (no attached console).
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class FFmpegError(RuntimeError):
    pass


def ensure_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise FFmpegError(
                f"'{tool}' was not found on PATH. Install FFmpeg and reopen the app."
            )


@dataclass
class MediaInfo:
    width: int
    height: int
    duration: float
    fps: float
    has_audio: bool


def probe(path: str | Path) -> MediaInfo:
    path = str(path)
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", path,
        ],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    if out.returncode != 0:
        raise FFmpegError(f"ffprobe failed:\n{out.stderr.strip()}")

    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        raise FFmpegError("No video stream found in the file.")

    width = int(v["width"])
    height = int(v["height"])

    # rotation metadata -> swap displayed dimensions
    rot = 0
    if "tags" in v and "rotate" in v["tags"]:
        try:
            rot = abs(int(v["tags"]["rotate"])) % 360
        except ValueError:
            rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = abs(int(sd["rotation"])) % 360
    if rot in (90, 270):
        width, height = height, width

    duration = 0.0
    if "duration" in data.get("format", {}):
        duration = float(data["format"]["duration"])
    elif v.get("duration"):
        duration = float(v["duration"])

    fps = 30.0
    rate = v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1"
    try:
        num, den = rate.split("/")
        if float(den) != 0:
            fps = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        fps = 30.0

    return MediaInfo(width, height, duration, fps, a is not None)


def extract_audio(
    src: str | Path,
    dst: str | Path,
    start: float = 0.0,
    duration: float | None = None,
) -> None:
    """16 kHz mono wav - the format Whisper wants.

    ``start`` / ``duration`` (seconds) trim the segment so the transcript -
    and therefore the caption timing - lines up with the trimmed render,
    which also starts at 0.
    """
    cmd = ["ffmpeg", "-y"]
    if start and start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if duration and duration > 0:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += [
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(dst),
    ]
    res = subprocess.run(
        cmd, capture_output=True, text=True, creationflags=_NO_WINDOW
    )
    if res.returncode != 0:
        raise FFmpegError(f"Audio extraction failed:\n{res.stderr.strip()}")


def run_with_progress(
    args: list[str],
    total_duration: float,
    on_progress: Callable[[float], None] | None = None,
    cwd: str | Path | None = None,
) -> None:
    """Run ffmpeg, parsing -progress so we can report a 0..1 fraction."""
    full = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            *args, "-progress", "pipe:1", "-nostats"]

    # stderr goes to a temp file, not a pipe: nobody reads stderr while the
    # process runs, and a filled pipe buffer would deadlock ffmpeg.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8",
                                errors="replace") as errf:
        proc = subprocess.Popen(
            full, stdout=subprocess.PIPE, stderr=errf,
            text=True, cwd=str(cwd) if cwd else None,
            creationflags=_NO_WINDOW,
        )

        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key == "out_time_us" and on_progress and total_duration > 0:
                try:
                    done = int(value) / 1_000_000.0
                    on_progress(max(0.0, min(1.0, done / total_duration)))
                except ValueError:
                    pass
            elif key == "progress" and value == "end" and on_progress:
                on_progress(1.0)

        proc.wait()
        if proc.returncode != 0:
            errf.seek(0)
            raise FFmpegError(f"ffmpeg render failed:\n{errf.read().strip()}")

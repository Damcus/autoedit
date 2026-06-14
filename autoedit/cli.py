"""Command-line entry point:  python -m autoedit.cli clip.mp4"""

from __future__ import annotations

import argparse
import sys

from .config import (ACCENT_COLORS, CAPTION_STYLES, FORMATS, REFRAME_MODES,
                     WHISPER_MODELS, Settings, parse_timecode)
from .pipeline import process


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="autoedit",
        description="Auto-edit a raw clip into a post-ready short.",
    )
    p.add_argument("input", help="source video file")
    p.add_argument("-o", "--output", help="output path (default: *_edited.mp4)")
    p.add_argument("--format", choices=list(FORMATS), default="vertical")
    p.add_argument("--captions", choices=CAPTION_STYLES, default="word")
    p.add_argument("--reframe", choices=REFRAME_MODES, default="smartcrop")
    p.add_argument("--model", choices=WHISPER_MODELS, default="small")
    p.add_argument("--language", default=None,
                   help="force language code (default: auto-detect)")
    p.add_argument("--accent", choices=list(ACCENT_COLORS), default="yellow",
                   help="highlight colour for word captions")
    p.add_argument("--start", default=None,
                   help="trim start, seconds or mm:ss (default: 0)")
    p.add_argument("--duration", default=None,
                   help="clip length, seconds or mm:ss (default: to the end)")
    args = p.parse_args(argv)

    try:
        clip_start = parse_timecode(args.start) or 0.0
        clip_duration = parse_timecode(args.duration)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    settings = Settings(
        fmt=args.format,
        caption_style=args.captions,
        reframe=args.reframe,
        model=args.model,
        language=args.language,
        accent_color=ACCENT_COLORS[args.accent],
        clip_start=clip_start,
        clip_duration=clip_duration,
    )

    def bar(frac: float, msg: str) -> None:
        width = 30
        filled = int(width * frac)
        sys.stdout.write(
            f"\r[{'#' * filled}{'-' * (width - filled)}] "
            f"{frac * 100:5.1f}%  {msg:<28}"
        )
        sys.stdout.flush()

    try:
        out = process(args.input, settings, args.output,
                       log=lambda m: print(f"\n{m}"), progress=bar)
    except Exception as exc:  # noqa: BLE001 - surface a clean message
        print(f"\nError: {exc}", file=sys.stderr)
        return 1

    print(f"\n\nSaved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

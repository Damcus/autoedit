"""User-tunable settings for one edit job."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Output canvas presets (width, height).
FORMATS = {
    "vertical": (1080, 1920),   # TikTok / Reels / Shorts
    "horizontal": (1920, 1080),  # YouTube landscape
    "square": (1080, 1080),     # Instagram feed
}

CAPTION_STYLES = ("word", "clean", "none")
REFRAME_MODES = ("smartcrop", "blur", "pad")
WHISPER_MODELS = ("tiny", "base", "small", "medium")

# Highlight colour of the active word (ASS &HAABBGGRR).
ACCENT_COLORS = {
    "yellow": "&H0000FFFF",
    "green": "&H0000FF00",
    "red": "&H000000FF",
    "blue": "&H00FF4000",
    "pink": "&H00FF00FF",
    "orange": "&H0000A5FF",
    "cyan": "&H00FFFF00",
}

# "auto" = let Whisper detect the language.
LANGUAGES = ("auto", "en", "sk", "cs", "de", "es", "fr",
             "it", "pl", "hu", "pt", "ru", "uk")


@dataclass
class Settings:
    fmt: str = "vertical"
    caption_style: str = "word"
    reframe: str = "smartcrop"
    model: str = "small"
    language: str | None = None        # None = auto-detect

    # Trim (seconds). clip_start = where the clip begins; clip_duration = how
    # long it runs (None = to the end of the source).
    clip_start: float = 0.0
    clip_duration: float | None = None

    # Caption look
    font: str = "Arial Black"
    font_size_ratio: float = 0.052     # of canvas height
    base_color: str = "&H00FFFFFF"     # white  (AABBGGRR)
    accent_color: str = "&H0000FFFF"   # yellow highlight
    outline: int = 6
    shadow: int = 3
    caption_v_ratio: float = 0.20      # bottom margin as fraction of height
    max_words_per_line: int = 5
    max_chars_per_line: int = 24

    # Audio
    loudness_i: float = -16.0          # integrated LUFS (good for social)
    loudness_tp: float = -1.5          # true peak
    loudness_lra: float = 11.0

    # Encode
    crf: int = 19
    preset: str = "medium"
    audio_bitrate: str = "192k"

    def canvas(self) -> tuple[int, int]:
        return FORMATS[self.fmt]

    def validate(self) -> None:
        if self.fmt not in FORMATS:
            raise ValueError(f"format must be one of {list(FORMATS)}")
        if self.caption_style not in CAPTION_STYLES:
            raise ValueError(f"caption_style must be one of {CAPTION_STYLES}")
        if self.reframe not in REFRAME_MODES:
            raise ValueError(f"reframe must be one of {REFRAME_MODES}")
        if self.model not in WHISPER_MODELS:
            raise ValueError(f"model must be one of {WHISPER_MODELS}")
        if self.clip_start < 0:
            raise ValueError("clip_start must be >= 0")
        if self.clip_duration is not None and self.clip_duration <= 0:
            raise ValueError("clip_duration must be > 0")


def parse_timecode(text: str | None) -> float | None:
    """Parse a time into seconds.

    Accepts ``ss``, ``mm:ss`` or ``hh:mm:ss`` (parts may be fractional).
    Empty / ``None`` returns ``None`` (meaning "unset"). Raises ``ValueError``
    on anything unparseable so callers can show a friendly message.
    """
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        if len(parts) > 3:
            raise ValueError(f"Invalid time: {text!r}")
        try:
            nums = [float(p) for p in parts]
        except ValueError as exc:
            raise ValueError(f"Invalid time: {text!r}") from exc
        seconds = 0.0
        for n in nums:
            if n < 0:
                raise ValueError(f"Invalid time: {text!r}")
            seconds = seconds * 60 + n
        return seconds
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"Invalid time: {text!r}") from exc


def default_output_path(src: str | Path) -> Path:
    src = Path(src)
    return src.with_name(f"{src.stem}_edited.mp4")

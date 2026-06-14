"""Generate an .ass subtitle file.

Two styles:
  * "word"  - the whole short phrase stays on screen, the currently spoken
              word is enlarged + accent-coloured and moves along (the
              classic high-retention TikTok / Shorts caption look).
  * "clean" - one calm line at a time, no per-word highlight.
"""

from __future__ import annotations

from typing import List

from .config import Settings
from .transcribe import Word

_SENT_END = (".", "!", "?", "…", ":", ";")

# Left/right margin baked into the style (px on the caption canvas).
MARGIN_LR = 60


def _fit_chars(s: Settings) -> int:
    """How many characters of the caption font fit on one line.

    The configured ``max_chars_per_line`` (24) overflows a 1080-wide vertical
    frame because Arial Black is very wide at ~100 px. Derive a real limit
    from the canvas width, side margins and font size so phrases never run off
    the edges. Conservative on purpose: leaves headroom for the active-word
    pop and the outline, and a 10% safety margin.
    """
    w, h = s.canvas()
    size = max(20, int(round(h * s.font_size_ratio)))
    avail = max(1, w - 2 * MARGIN_LR)
    # Arial Black averages ~0.62 em per glyph; round down hard.
    char_w = size * 0.62
    return max(6, int((avail * 0.90) / char_w))


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _sanitize(text: str) -> str:
    # Braces start override blocks in ASS; newlines break the event line.
    return text.replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def _chunk(words: List[Word], s: Settings) -> List[List[Word]]:
    """Split the word stream into short on-screen phrases."""
    chunks: List[List[Word]] = []
    cur: List[Word] = []
    cur_chars = 0

    # Cap by whichever is tighter: the configured limit or what actually fits.
    char_cap = min(s.max_chars_per_line, _fit_chars(s))

    for i, w in enumerate(words):
        wlen = len(w.text) + (1 if cur else 0)
        gap_next = (words[i + 1].start - w.end) if i + 1 < len(words) else 0.0

        # Hard width ceiling: if this word won't fit, close the line *before*
        # adding it, so a rendered line never exceeds char_cap characters.
        if cur and cur_chars + wlen > char_cap:
            chunks.append(cur)
            cur, cur_chars = [], 0
            wlen = len(w.text)  # first word on the new line: no leading space

        cur.append(w)
        cur_chars += wlen

        ends_sentence = w.text.rstrip().endswith(_SENT_END)
        big_pause = gap_next > 0.7

        if (len(cur) >= s.max_words_per_line or big_pause
                or (ends_sentence and len(cur) >= 2)):
            chunks.append(cur)
            cur, cur_chars = [], 0

    if cur:
        chunks.append(cur)
    return chunks


def _style_block(s: Settings, w: int, h: int) -> str:
    size = max(20, int(round(h * s.font_size_ratio)))
    margin_v = int(round(h * s.caption_v_ratio))
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {w}\n"
        f"PlayResY: {h}\n"
        # 0 = smart auto-wrap: a long phrase wraps to a second line instead of
        # running off the sides (safety net on top of the per-line char cap).
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Main,{s.font},{size},{s.base_color},&H000000FF,"
        f"&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"{s.outline},{s.shadow},2,{MARGIN_LR},{MARGIN_LR},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "Effect, Text\n"
    )


def _dialogue(start: float, end: float, text: str) -> str:
    if end <= start:
        end = start + 0.05
    return (f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},"
            f"Main,,0,0,0,{text}\n")


def build_ass(words: List[Word], s: Settings, out_path: str) -> None:
    w, h = s.canvas()
    lines = [_style_block(s, w, h)]

    if s.caption_style == "none" or not words:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("".join(lines))
        return

    chunks = _chunk(words, s)

    for chunk in chunks:
        clean = [_sanitize(c.text) for c in chunk]

        if s.caption_style == "clean":
            text = "{\\fad(60,60)}" + " ".join(clean)
            lines.append(_dialogue(chunk[0].start, chunk[-1].end, text))
            continue

        # "word": one event per word, full phrase visible, active word popped.
        for idx, cw in enumerate(chunk):
            seg_start = cw.start
            seg_end = (chunk[idx + 1].start if idx + 1 < len(chunk)
                       else cw.end + 0.10)

            parts = []
            for j, token in enumerate(clean):
                if j == idx:
                    parts.append(
                        f"{{\\1c{s.accent_color}\\fscx112\\fscy112}}"
                        f"{token}"
                        f"{{\\1c{s.base_color}\\fscx100\\fscy100}}"
                    )
                else:
                    parts.append(token)
            body = " ".join(parts)

            fade = ""
            if idx == 0:
                fade = "{\\fad(70,0)}"
            elif idx == len(chunk) - 1:
                fade = "{\\fad(0,70)}"

            lines.append(_dialogue(seg_start, seg_end, fade + body))

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(lines))

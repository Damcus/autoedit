# AutoEdit

Drop in a raw clip (podcast cut, talking-head, gameplay commentary...) and get
back a **post-ready vertical short**: word-by-word captions, the speaker kept
in frame, and broadcast-level audio — in one click.

## What it does

| Stage | What happens |
|-------|--------------|
| Transcribe | `faster-whisper` makes a word-level transcript (GPU if available, CPU otherwise). |
| Captions | Animated word-by-word highlight captions (the high-retention Shorts/Reels style). |
| Reframe | Finds the speaker's face and crops to a stable 9:16 frame. |
| Audio | High-pass + gentle compression + EBU R128 loudness (`-16 LUFS`) so it's loud and clean. |
| Render | Single ffmpeg pass → `H.264 / AAC` MP4 with `+faststart`. |

## Requirements

- **Python 3.10+** 
- **FFmpeg** on PATH 
- ~1 GB disk for the speech model (downloaded once, first run)

## Setup (once)

Double-click **`install.bat`** — it makes a virtual environment and installs
everything. (Or manually: `pip install -r requirements.txt`.)

## Use

Double-click **`run.bat`** → drag a video onto the window → **Edit clip**.
The finished file is saved next to the original as `name_edited.mp4`.

### Options in the window

- **Trim** — keep just part of the clip. **Start at** is where the clip
  begins (`mm:ss`, e.g. `1:30`); **Clip length** is how long it runs
  (`mm:ss`, or leave blank for the whole clip). A live hint shows the
  resulting output length and range as you type.
- **Format** — `vertical` (9:16, default) · `horizontal` · `square`
- **Captions** — `word` (animated highlight, default) · `clean` · `none`
- **Reframe** — `smartcrop` (follow speaker, default) · `blur` (blurred-fill,
  nothing cropped) · `pad` (letterbox)
- **Quality** — Whisper model size: `tiny`→`medium`. `small` is the sweet
  spot; `medium` is most accurate but slower.
- **Language** — `auto` (detect, default) or force a code (`sk`, `en`, ...).
  Forcing the right language speeds transcription up and improves accuracy.
- **Highlight** — colour of the active word in `word` captions:
  `yellow` (default) · `green` · `red` · `blue` · `pink` · `orange` · `cyan`

The app remembers your last-used options (stored in `~/.autoedit.json`),
shows the clip's resolution/length once loaded, and offers **Play** /
**Open output folder** buttons when the edit finishes.

## Command line (optional)

```powershell
.venv\Scripts\activate
python -m autoedit.cli myclip.mp4
python -m autoedit.cli podcast.mp4 --captions word --reframe blur --model medium -o out.mp4
python -m autoedit.cli talk.mp4 --language sk --accent green
python -m autoedit.cli podcast.mp4 --start 1:30 --duration 0:45   # keep 0:45 from 1:30
```

## Notes

- First run downloads the speech model — later runs are much faster.
- GPU (your RTX 2050) is used automatically when the CUDA runtime is present;
  otherwise it falls back to CPU with no change in output quality.
- Captions use the **Arial Black** system font by default; change `font` in
  `autoedit/config.py` to any installed font.

"""AutoEdit - drag-and-drop desktop app.

Drop a clip, pick the look, choose how much of it to keep, click Edit. The
heavy work runs on a background thread; the UI stays responsive and streams
progress + a log.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk


def _reexec_in_venv() -> None:
    """If launched with a Python outside the project venv (VS Code Run,
    plain `python app.py`), relaunch with .venv so dependencies resolve."""
    if sys.prefix != sys.base_prefix:
        return  # already inside a virtual environment
    root = Path(__file__).resolve().parent
    venv_py = root / (".venv/Scripts/python.exe" if os.name == "nt"
                      else ".venv/bin/python")
    if venv_py.exists():
        raise SystemExit(subprocess.call(
            [str(venv_py), str(root / "app.py"), *sys.argv[1:]]))


_reexec_in_venv()

from autoedit import __version__  # noqa: E402
from autoedit import ffmpeg_utils as ff  # noqa: E402
from autoedit.config import (ACCENT_COLORS, CAPTION_STYLES, FORMATS,  # noqa: E402
                             LANGUAGES, REFRAME_MODES, WHISPER_MODELS,
                             Settings, parse_timecode)
from autoedit.pipeline import process  # noqa: E402

# Optional real drag-and-drop. Falls back to the Browse button if missing.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND = True
except Exception:  # noqa: BLE001
    _DND = False

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv"}
PREFS_PATH = Path.home() / ".autoedit.json"

# ------------------------------------------------------------------ palette
BG = "#121419"          # window background
CARD = "#1c1f27"        # panels / inputs
CARD_HI = "#23262f"     # drop-zone hover fill
ACCENT = "#ffd23f"      # brand yellow
ACCENT_HOVER = "#ffdf6b"
TEXT = "#eceef2"
MUTED = "#8a8f99"
FAINT = "#5d626d"       # section labels
BORDER = "#2c2f38"
BTN_BG = "#2c2f38"
BTN_HOVER = "#3a3e49"
ERROR = "#ff6b6b"
OK = "#5ad17e"


def _hover(widget: tk.Widget, normal: str, hover: str) -> None:
    widget.bind("<Enter>", lambda e: widget.configure(bg=hover))
    widget.bind("<Leave>", lambda e: widget.configure(bg=normal))


def _fmt_dur(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.src: str | None = None
        self.src_duration: float | None = None
        self.last_output: str | None = None
        self.prefs = self._load_prefs()

        root.title(f"AutoEdit {__version__} - clip to post-ready short")
        root.geometry("700x880")
        root.minsize(600, 780)
        root.configure(bg=BG)

        self._init_style()
        self._build_ui()
        self._poll_queue()

    # ---------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        # ---- Header -----------------------------------------------------
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", pady=(22, 0))
        title = tk.Frame(header, bg=BG)
        title.pack()
        tk.Label(title, text="Auto", bg=BG, fg=TEXT,
                 font=("Segoe UI", 24, "bold")).pack(side="left")
        tk.Label(title, text="Edit", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 24, "bold")).pack(side="left")
        tk.Label(header,
                 text="Turn a raw clip into a captioned, reframed, "
                      "post-ready short.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(pady=(4, 0))

        # ---- Body (consistent side padding) -----------------------------
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=30, pady=(18, 18))

        # ---- Drop zone --------------------------------------------------
        self.drop = tk.Label(
            body,
            text=("Drop your video here\n" if _DND
                  else "Click to choose a video\n")
                 + ".mp4   .mov   .mkv   .webm   .avi  ...",
            bg=CARD, fg=MUTED, font=("Segoe UI", 12),
            height=5, relief="flat", bd=0, cursor="hand2",
            highlightthickness=2, highlightbackground=BORDER,
            highlightcolor=BORDER,
        )
        self.drop.pack(fill="x")
        self.drop.bind("<Button-1>", lambda e: self._browse())

        if _DND:
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<Drop>>", self._on_drop)
            self.drop.dnd_bind("<<DropEnter>>", self._on_drag_enter)
            self.drop.dnd_bind("<<DropLeave>>", self._on_drag_leave)

        # Clip details + Browse on one row beneath the drop zone.
        sub = tk.Frame(body, bg=BG)
        sub.pack(fill="x", pady=(8, 0))
        self.src_info = tk.Label(sub, text="No clip loaded", bg=BG, fg=MUTED,
                                 font=("Segoe UI", 9))
        self.src_info.pack(side="left")
        browse = tk.Button(sub, text="Browse...", command=self._browse,
                           bg=BTN_BG, fg=TEXT, activebackground=BTN_HOVER,
                           activeforeground=TEXT, relief="flat", bd=0,
                           cursor="hand2", font=("Segoe UI", 9),
                           padx=14, pady=5)
        browse.pack(side="right")
        _hover(browse, BTN_BG, BTN_HOVER)

        # ---- Trim section ----------------------------------------------
        self._section(body, "Trim").pack(fill="x", pady=(18, 8))
        trim = tk.Frame(body, bg=BG)
        trim.pack(fill="x")

        fields = tk.Frame(trim, bg=BG)
        fields.pack(fill="x")
        self.var_start = tk.StringVar(value="0:00")
        self.var_len = tk.StringVar(value="")
        self._timefield(fields, "Start at", self.var_start,
                        "mm:ss   (e.g. 1:30)", 0)
        self._timefield(fields, "Clip length", self.var_len,
                        "mm:ss   (blank = whole clip)", 1)

        self.trim_hint = tk.Label(trim, text="Blank length keeps the whole clip.",
                                  bg=BG, fg=MUTED, font=("Segoe UI", 9),
                                  anchor="w")
        self.trim_hint.pack(fill="x", pady=(8, 0))
        self.var_start.trace_add("write", self._update_trim_hint)
        self.var_len.trace_add("write", self._update_trim_hint)

        # ---- Look section ----------------------------------------------
        self._section(body, "Look & quality").pack(fill="x", pady=(18, 8))
        opts = tk.Frame(body, bg=BG)
        opts.pack(fill="x")
        for c in range(3):
            opts.columnconfigure(c, weight=1)
        self.var_fmt = self._dropdown(opts, "Format", list(FORMATS),
                                      self._pref("fmt", FORMATS, "vertical"),
                                      0, 0)
        self.var_cap = self._dropdown(opts, "Captions", list(CAPTION_STYLES),
                                      self._pref("cap", CAPTION_STYLES,
                                                 "word"), 0, 1)
        self.var_ref = self._dropdown(opts, "Reframe", list(REFRAME_MODES),
                                      self._pref("ref", REFRAME_MODES,
                                                 "smartcrop"), 0, 2)
        self.var_mdl = self._dropdown(opts, "Quality", list(WHISPER_MODELS),
                                      self._pref("mdl", WHISPER_MODELS,
                                                 "small"), 1, 0)
        self.var_lang = self._dropdown(opts, "Language", list(LANGUAGES),
                                       self._pref("lang", LANGUAGES, "auto"),
                                       1, 1)
        self.var_acc = self._dropdown(opts, "Highlight", list(ACCENT_COLORS),
                                      self._pref("acc", ACCENT_COLORS,
                                                 "yellow"), 1, 2)

        # ---- Action -----------------------------------------------------
        self.btn = tk.Button(body, text="Edit clip", command=self._start,
                             bg=ACCENT, fg=BG, relief="flat", bd=0,
                             activebackground=ACCENT_HOVER, cursor="hand2",
                             font=("Segoe UI", 13, "bold"),
                             padx=20, pady=11, state="disabled")
        self.btn.pack(fill="x", pady=(20, 8))
        _hover(self.btn, ACCENT, ACCENT_HOVER)

        self.pbar = ttk.Progressbar(body, mode="determinate", maximum=100.0)
        self.pbar.pack(fill="x", pady=2)
        self.status = tk.Label(body, text="Waiting for a clip...",
                               bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.status.pack(anchor="w", pady=(2, 0))

        self.log = tk.Text(body, height=7, bg="#0f1115", fg=MUTED,
                           relief="flat", bd=0, font=("Consolas", 9),
                           wrap="word", padx=12, pady=8,
                           highlightthickness=1, highlightbackground=BORDER)
        self.log.pack(fill="both", expand=True, pady=(10, 4))
        self.log.configure(state="disabled")

        # Shown once a job finishes.
        self.done_row = tk.Frame(body, bg=BG)
        for text, cmd in (("Play", self._play),
                          ("Open output folder", self._open_folder)):
            b = tk.Button(self.done_row, text=text, command=cmd,
                          bg=BTN_BG, fg=TEXT, relief="flat", bd=0,
                          activebackground=BTN_HOVER, activeforeground=TEXT,
                          cursor="hand2", font=("Segoe UI", 10),
                          padx=14, pady=7)
            b.pack(side="left", padx=(0, 10))
            _hover(b, BTN_BG, BTN_HOVER)

    def _section(self, parent, text):
        """A faint uppercase label with a hairline rule beside it."""
        f = tk.Frame(parent, bg=BG)
        tk.Label(f, text=text.upper(), bg=BG, fg=FAINT,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        rule = tk.Frame(f, bg=BORDER, height=1)
        rule.pack(side="left", fill="x", expand=True, padx=(12, 0))
        return f

    def _timefield(self, parent, label, var, hint, col):
        parent.columnconfigure(col, weight=1)
        cell = tk.Frame(parent, bg=BG)
        cell.grid(row=0, column=col, sticky="ew",
                  padx=(0, 12) if col == 0 else (12, 0))
        tk.Label(cell, text=label, bg=BG, fg=TEXT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        entry = tk.Entry(cell, textvariable=var, bg=CARD, fg=TEXT,
                         insertbackground=ACCENT, relief="flat",
                         font=("Segoe UI", 12), justify="left",
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT)
        entry.pack(fill="x", ipady=5, pady=(4, 2))
        tk.Label(cell, text=hint, bg=BG, fg=FAINT,
                 font=("Segoe UI", 8)).pack(anchor="w")

    def _dropdown(self, parent, label, values, default, row, col):
        cell = tk.Frame(parent, bg=BG)
        cell.grid(row=row, column=col, padx=6, pady=6, sticky="ew")
        tk.Label(cell, text=label, bg=BG, fg=MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w")
        var = tk.StringVar(value=default)
        cb = ttk.Combobox(cell, textvariable=var, values=values,
                          state="readonly", font=("Segoe UI", 9))
        cb.pack(fill="x", pady=(3, 0))
        return var

    def _init_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TProgressbar", troughcolor=CARD,
                        background=ACCENT, borderwidth=0, thickness=12)
        style.configure("TCombobox", fieldbackground=CARD, background=BTN_BG,
                        foreground=TEXT, arrowcolor=TEXT,
                        bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD,
                        padding=5)
        style.map("TCombobox",
                  fieldbackground=[("readonly", CARD)],
                  foreground=[("readonly", TEXT)],
                  selectbackground=[("readonly", CARD)],
                  selectforeground=[("readonly", TEXT)],
                  bordercolor=[("focus", ACCENT)])
        # The dropdown list itself is a plain Listbox - style via options.
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", BG)

    # ------------------------------------------------------------ prefs
    @staticmethod
    def _load_prefs() -> dict:
        try:
            return json.loads(PREFS_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - missing/corrupt file = defaults
            return {}

    def _pref(self, key: str, allowed, default: str) -> str:
        val = self.prefs.get(key)
        return val if val in allowed else default

    def _save_prefs(self) -> None:
        data = {"fmt": self.var_fmt.get(), "cap": self.var_cap.get(),
                "ref": self.var_ref.get(), "mdl": self.var_mdl.get(),
                "lang": self.var_lang.get(), "acc": self.var_acc.get()}
        try:
            PREFS_PATH.write_text(json.dumps(data, indent=2),
                                  encoding="utf-8")
        except OSError:
            pass

    # -------------------------------------------------------- trim helper
    def _read_trim(self) -> tuple[float, float | None] | None:
        """Parse the two time fields. Returns (start, length) in seconds,
        or None if the input is invalid (status already set)."""
        try:
            start = parse_timecode(self.var_start.get()) or 0.0
            length = parse_timecode(self.var_len.get())
        except ValueError:
            self._set_status("Times must look like mm:ss or seconds.",
                             error=True)
            return None
        if start < 0 or (length is not None and length <= 0):
            self._set_status("Trim times must be positive.", error=True)
            return None
        if self.src_duration is not None and start >= self.src_duration:
            self._set_status("Start is past the end of the clip.", error=True)
            return None
        return start, length

    def _update_trim_hint(self, *_args) -> None:
        if not hasattr(self, "trim_hint"):
            return
        try:
            start = parse_timecode(self.var_start.get()) or 0.0
            length = parse_timecode(self.var_len.get())
        except ValueError:
            self.trim_hint.configure(text="Use mm:ss or plain seconds.",
                                     fg=ERROR)
            return
        if start < 0 or (length is not None and length <= 0):
            self.trim_hint.configure(text="Times must be positive.", fg=ERROR)
            return

        total = self.src_duration
        if total is None:
            if length:
                self.trim_hint.configure(
                    text=f"Will keep {_fmt_dur(length)} from "
                         f"{_fmt_dur(start)}.", fg=MUTED)
            else:
                self.trim_hint.configure(
                    text="Blank length keeps the whole clip.", fg=MUTED)
            return
        if start >= total:
            self.trim_hint.configure(
                text=f"Start is past the clip end ({_fmt_dur(total)}).",
                fg=ERROR)
            return

        end = total if not length else min(start + length, total)
        out = end - start
        active = start > 0 or bool(length)
        self.trim_hint.configure(
            text=f"Output {_fmt_dur(out)}   ·   {_fmt_dur(start)} → "
                 f"{_fmt_dur(end)}  of  {_fmt_dur(total)}",
            fg=ACCENT if active else MUTED)

    # -------------------------------------------------------------- events
    def _on_drag_enter(self, event):
        if not self.busy:
            self.drop.configure(highlightbackground=ACCENT, bg=CARD_HI)
        return event.action

    def _on_drag_leave(self, event):
        self.drop.configure(highlightbackground=BORDER, bg=CARD)
        return getattr(event, "action", None)

    def _on_drop(self, event) -> None:
        self.drop.configure(highlightbackground=BORDER, bg=CARD)
        if self.busy:
            return
        paths = self.root.tk.splitlist(event.data)
        if paths:
            self._set_source(paths[0])

    def _browse(self) -> None:
        if self.busy:
            return
        path = filedialog.askopenfilename(
            title="Choose a video clip",
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.webm *.avi *.m4v "
                                 "*.flv *.wmv"), ("All files", "*.*")],
        )
        if path:
            self._set_source(path)

    def _set_source(self, path: str) -> None:
        if self.busy:
            return
        path = path.strip().strip("{}")
        ext = Path(path).suffix.lower()
        if ext not in VIDEO_EXTS:
            self._set_status(f"Not a supported video: {ext}", error=True)
            return
        self.src = path
        self.src_duration = None
        self.drop.configure(text=f"Loaded\n{Path(path).name}", fg=TEXT)
        self.src_info.configure(text="Reading clip...")
        self.btn.configure(state="normal", text="Edit clip")
        self._set_status("Ready. Set a trim, then click 'Edit clip'.")
        self.done_row.pack_forget()
        self._update_trim_hint()
        self._probe_async(path)

    def _probe_async(self, path: str) -> None:
        """Fill the clip-details line without blocking the UI."""
        def job() -> None:
            try:
                info = ff.probe(path)
            except Exception:  # noqa: BLE001 - cosmetic only
                self.q.put(("srcinfo", (None, "Could not read clip details")))
                return
            text = (f"{info.width} x {info.height}   |   "
                    f"{_fmt_dur(info.duration)}   |   {info.fps:.0f} fps")
            if not info.has_audio:
                text += "   |   no audio (captions skipped)"
            self.q.put(("srcinfo", (info.duration, text)))

        threading.Thread(target=job, daemon=True).start()

    # --------------------------------------------------------------- run
    def _start(self) -> None:
        if self.busy or not self.src:
            return
        trim = self._read_trim()
        if trim is None:
            return
        clip_start, clip_len = trim

        self.busy = True
        self.btn.configure(state="disabled", text="Editing...")
        self.pbar["value"] = 0
        self._clear_log()
        self.done_row.pack_forget()
        self._save_prefs()

        lang = self.var_lang.get()
        settings = Settings(
            fmt=self.var_fmt.get(),
            caption_style=self.var_cap.get(),
            reframe=self.var_ref.get(),
            model=self.var_mdl.get(),
            language=None if lang == "auto" else lang,
            accent_color=ACCENT_COLORS.get(self.var_acc.get(),
                                           ACCENT_COLORS["yellow"]),
            clip_start=clip_start,
            clip_duration=clip_len,
        )
        threading.Thread(target=self._worker, args=(self.src, settings),
                         daemon=True).start()

    def _worker(self, src: str, settings: Settings) -> None:
        try:
            out = process(
                src, settings,
                log=lambda m: self.q.put(("log", m)),
                progress=lambda f, msg: self.q.put(("prog", (f, msg))),
            )
            self.q.put(("done", out))
        except Exception as exc:  # noqa: BLE001
            self.q.put(("log", traceback.format_exc()))
            self.q.put(("error", str(exc)))

    # ---------------------------------------------------------- queue pump
    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "prog":
                    frac, msg = payload
                    self.pbar["value"] = frac * 100.0
                    self._set_status(
                        f"{frac * 100.0:.0f}%   {msg or 'Working...'}")
                elif kind == "srcinfo":
                    duration, text = payload
                    self.src_duration = duration
                    self.src_info.configure(text=text)
                    self._update_trim_hint()
                elif kind == "done":
                    self._on_done(payload)
                elif kind == "error":
                    self._on_error(payload)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    def _on_done(self, out: str) -> None:
        self.busy = False
        self.last_output = out
        self.pbar["value"] = 100
        self.btn.configure(state="normal", text="Edit another")
        self._set_status(f"Done: {Path(out).name}", ok=True)
        self.done_row.pack(anchor="w", pady=(4, 0))

    def _on_error(self, msg: str) -> None:
        self.busy = False
        self.pbar["value"] = 0
        self.btn.configure(state="normal", text="Edit clip")
        self._set_status(f"Error: {msg}", error=True)

    # ------------------------------------------------------------- helpers
    def _set_status(self, text: str, error=False, ok=False) -> None:
        color = ERROR if error else (OK if ok else MUTED)
        self.status.configure(text=text, fg=color)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _play(self) -> None:
        if not self.last_output:
            return
        try:
            if sys.platform == "win32":
                os.startfile(self.last_output)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self.last_output])
            else:
                subprocess.Popen(["xdg-open", self.last_output])
        except Exception:  # noqa: BLE001
            self._set_status(f"Saved in: {self.last_output}")

    def _open_folder(self) -> None:
        if not self.last_output:
            return
        folder = str(Path(self.last_output).parent)
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,",
                                  os.path.normpath(self.last_output)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception:  # noqa: BLE001
            self._set_status(f"Saved in: {folder}")


def main() -> None:
    root = TkinterDnD.Tk() if _DND else tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

"""Speech-to-text with word-level timestamps via faster-whisper.

Tries CUDA first (RTX cards), and transparently falls back to CPU int8 if
the NVIDIA runtime DLLs (cuBLAS / cuDNN) are missing. The catch: creating a
WhisperModel with device="cuda" succeeds even when those DLLs are absent --
the failure only surfaces once inference actually runs. So the fallback has
to wrap the transcription itself, not just model construction.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


@dataclass
class Word:
    start: float
    end: float
    text: str


def _make_model(model_size: str, device: str):
    from faster_whisper import WhisperModel

    compute = "float16" if device == "cuda" else "int8"
    return WhisperModel(model_size, device=device, compute_type=compute)


def _run(
    model,
    audio_path: str,
    language: str | None,
    total: float,
    on_progress: Callable[[float], None] | None,
):
    """Consume the generator fully. Any CUDA failure raises here."""
    segments, info = model.transcribe(
        audio_path,
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        beam_size=5,
    )

    words: list[Word] = []
    dur = total or getattr(info, "duration", 0.0) or 0.0

    for seg in segments:  # iteration is what actually triggers inference
        if seg.words:
            for w in seg.words:
                txt = (w.word or "").strip()
                if not txt:
                    continue
                start = w.start if w.start is not None else seg.start
                end = w.end if w.end is not None else seg.end
                if start is None or end is None:
                    continue
                if end <= start:
                    end = start + 0.15
                words.append(Word(float(start), float(end), txt))
        elif seg.text.strip():
            words.append(Word(float(seg.start), float(seg.end),
                              seg.text.strip()))

        if on_progress and dur > 0:
            on_progress(max(0.0, min(1.0, float(seg.end) / dur)))

    if on_progress:
        on_progress(1.0)
    return words, info


def transcribe(
    audio_path: str,
    model_size: str = "small",
    language: str | None = None,
    duration: float = 0.0,
    log: Callable[[str], None] = print,
    on_progress: Callable[[float], None] | None = None,
) -> list[Word]:
    try:
        import faster_whisper  # noqa: F401
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "faster-whisper is not installed in this Python environment. "
            "Run install.bat once, then start the app with run.bat."
        ) from exc

    attempts = [("cuda", "GPU (CUDA)"), ("cpu", "CPU")]

    last_err: Exception | None = None
    for device, label in attempts:
        try:
            log(f"Loading speech model on {label} (model: {model_size})...")
            model = _make_model(model_size, device)
            words, info = _run(model, audio_path, language, duration,
                               on_progress)
            log(f"Detected language: {info.language} "
                f"({info.language_probability:.0%} confidence)")
            log(f"Transcribed {len(words)} words on {label}.")
            return words
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if device == "cuda":
                log(f"GPU not usable ({exc}). Falling back to CPU...")
                continue
            raise

    raise RuntimeError(f"Transcription failed: {last_err}")

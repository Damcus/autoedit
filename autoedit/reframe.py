"""Work out a stable crop rectangle that keeps the speaker in frame.

We sample frames across the clip, find faces with OpenCV's bundled Haar
cascade (no model download needed), take the median face centre, and build
one fixed crop window of the target aspect ratio. A single stable crop looks
far cleaner on talking-head podcast clips than a jittery per-frame follow.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Callable, Optional


@dataclass
class Crop:
    w: int
    h: int
    x: int
    y: int


def _even(n: int) -> int:
    n = int(round(n))
    return n - (n % 2)


def _fit_rect(src_w: int, src_h: int, target_ar: float) -> tuple[int, int]:
    """Largest w,h with w/h == target_ar that fits inside the source."""
    if src_w / src_h > target_ar:        # source too wide -> limit by height
        h = src_h
        w = int(round(h * target_ar))
    else:                                # source too tall -> limit by width
        w = src_w
        h = int(round(w / target_ar))
    return _even(min(w, src_w)), _even(min(h, src_h))


def compute_crop(
    video_path: str,
    target_w: int,
    target_h: int,
    log: Callable[[str], None] = print,
    max_samples: int = 45,
    start: float = 0.0,
    duration: float | None = None,
) -> Optional[Crop]:
    """Return a Crop, or None if no reframing is needed (already that ratio).

    ``start`` / ``duration`` (seconds) limit face sampling to the trimmed
    window, so the crop centres on whoever is on screen in the kept segment.
    """
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        log("Could not open video for face analysis; using centre crop.")
        return None

    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    target_ar = target_w / target_h

    if src_w == 0 or src_h == 0:
        cap.release()
        return None

    # Already the right aspect (within 1%) -> no crop needed.
    if abs((src_w / src_h) - target_ar) < 0.01:
        cap.release()
        return None

    crop_w, crop_h = _fit_rect(src_w, src_h, target_ar)

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    # Restrict sampling to the trimmed window (in frames) when we know fps.
    start_frame = int(round(start * fps)) if (start > 0 and fps > 0) else 0
    if duration and duration > 0 and fps > 0:
        window = int(round(duration * fps))
    else:
        window = (n_frames - start_frame) if n_frames else 0
    if start_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    step = max(1, window // max_samples) if window else 15
    centers_x: list[float] = []
    centers_y: list[float] = []
    idx = 0

    while True:
        if window and idx >= window:
            break
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if ok and frame is not None:
                scale = 640.0 / max(1, frame.shape[1])
                small = cv2.resize(frame, None, fx=scale, fy=scale)
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                faces = cascade.detectMultiScale(gray, 1.1, 5,
                                                 minSize=(40, 40))
                if len(faces):
                    # Largest face = the person closest / main speaker.
                    fx, fy, fw, fh = max(faces, key=lambda r: r[2] * r[3])
                    centers_x.append((fx + fw / 2) / scale)
                    centers_y.append((fy + fh / 2) / scale)
        idx += 1

    cap.release()

    if centers_x:
        cx = median(centers_x)
        cy = median(centers_y)
        log(f"Found a speaker in {len(centers_x)} sampled frames; "
            "centring the crop on them.")
    else:
        cx, cy = src_w / 2.0, src_h / 2.0
        log("No clear face found; using a centred crop.")

    x = int(round(cx - crop_w / 2))
    y = int(round(cy - crop_h / 2))
    # A face sits high in the frame; nudge so the head is not chopped.
    y -= int(crop_h * 0.06)

    x = max(0, min(x, src_w - crop_w))
    y = max(0, min(y, src_h - crop_h))
    return Crop(crop_w, crop_h, _even(x), _even(y))

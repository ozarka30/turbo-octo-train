"""Screen capture of the dialogue region with cheap change detection.

We only call Claude when the region has visibly changed and then held still for a
couple of frames (so we do not OCR half-typed text while the game is still
animating the line in).
"""

from __future__ import annotations

import logging
import time
from typing import Iterator, Optional

import numpy as np
from PIL import Image

from .config import Region

log = logging.getLogger(__name__)

_THUMB = (96, 32)


def _signature(image: Image.Image) -> np.ndarray:
    small = image.convert("L").resize(_THUMB, Image.BILINEAR)
    return np.asarray(small, dtype=np.int16)


def frame_difference(a: Image.Image, b: Image.Image) -> float:
    """Fraction of thumbnail pixels that differ noticeably between two frames (0.0 to 1.0)."""
    da, db = _signature(a), _signature(b)
    return float(np.mean(np.abs(da - db) > 24))


class ChangeDetector:
    """Decides whether a new frame is worth sending to OCR."""

    def __init__(self, threshold: float = 0.02, stability_frames: int = 2):
        self.threshold = threshold
        self.stability_frames = max(1, stability_frames)
        self._last_sent: Optional[Image.Image] = None
        self._candidate: Optional[Image.Image] = None
        self._stable_count = 0

    def offer(self, frame: Image.Image) -> Optional[Image.Image]:
        """Feed a frame. Returns the frame once it is both new and stable, else None."""
        if self._last_sent is not None and frame_difference(frame, self._last_sent) < self.threshold:
            self._candidate = None
            self._stable_count = 0
            return None
        if self._candidate is not None and frame_difference(frame, self._candidate) < self.threshold:
            self._stable_count += 1
        else:
            self._candidate = frame
            self._stable_count = 1
        if self._stable_count >= self.stability_frames:
            self._last_sent = frame
            self._candidate = None
            self._stable_count = 0
            return frame
        return None


class ScreenGrabber:
    def __init__(self, region: Optional[Region] = None, monitor: int = 1):
        import mss  # imported lazily: needs a display

        self._sct = mss.mss()
        if region is None:
            mon = self._sct.monitors[monitor]
            region = (mon["left"], mon["top"], mon["width"], mon["height"])
        x, y, w, h = region
        self._box = {"left": x, "top": y, "width": w, "height": h}

    def grab(self) -> Image.Image:
        shot = self._sct.grab(self._box)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def watch(self, detector: ChangeDetector, interval: float = 0.5) -> Iterator[Image.Image]:
        while True:
            frame = self.grab()
            ready = detector.offer(frame)
            if ready is not None:
                yield ready
            time.sleep(interval)

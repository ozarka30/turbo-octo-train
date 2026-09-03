"""Screen capture of the dialogue region with cheap change detection.

We only call Claude when the region has visibly changed and then held still for a
couple of frames (so we do not OCR half-typed text while the game is still
animating the line in).
"""

from __future__ import annotations

import logging
import threading
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
        self.set_region(region)

    def set_region(self, region: Region) -> None:
        """Use this region from now on, clamped to the virtual screen."""
        x, y, w, h = region
        virt = self._sct.monitors[0]
        x0, y0 = max(x, virt["left"]), max(y, virt["top"])
        x1 = min(x + w, virt["left"] + virt["width"])
        y1 = min(y + h, virt["top"] + virt["height"])
        if x1 - x0 < 8 or y1 - y0 < 8:
            raise ValueError(f"region {region} lies outside the screen {virt}")
        if (x0, y0, x1 - x0, y1 - y0) != (x, y, w, h):
            log.warning("region %s clamped to the screen: %s", region, (x0, y0, x1 - x0, y1 - y0))
        self.region = (x0, y0, x1 - x0, y1 - y0)
        self._box = {"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}

    def grab(self) -> Image.Image:
        shot = self._sct.grab(self._box)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def watch(self, detector: ChangeDetector, interval: float = 0.5, stop: Optional[threading.Event] = None) -> Iterator[Image.Image]:
        stop = stop or threading.Event()
        while not stop.is_set():
            frame = self.grab()
            ready = detector.offer(frame)
            if ready is not None:
                yield ready
            stop.wait(interval)

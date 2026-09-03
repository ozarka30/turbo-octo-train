"""Disk cache for OCR results, keyed by the screenshot's pixels.

The same frame (a title screen, a menu you keep returning to) never needs a
second vision call.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from pathlib import Path
from typing import Optional

from PIL import Image

from .lesson import OcrResult

log = logging.getLogger(__name__)


def png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


class OcrCache:
    def __init__(self, cache_dir: Path, model: str):
        self.dir = Path(cache_dir) / "ocr"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.hits = 0

    def _path(self, png: bytes) -> Path:
        key = hashlib.sha1(self.model.encode("utf-8") + b"\0" + png).hexdigest()
        return self.dir / f"{key}.json"

    def get(self, png: bytes) -> Optional[OcrResult]:
        p = self._path(png)
        if not p.exists():
            return None
        try:
            result = OcrResult.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        self.hits += 1
        log.debug("ocr cache hit %s", p.name)
        return result

    def put(self, png: bytes, result: OcrResult) -> None:
        # A frame caught mid-typing must be re-read next time, never served from disk.
        if any(not line.complete for line in result.lines):
            return
        self._path(png).write_text(result.model_dump_json(), encoding="utf-8")

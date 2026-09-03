"""Runtime settings, loaded from environment variables with sensible defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

Region = Tuple[int, int, int, int]  # x, y, width, height


def parse_region(value: str) -> Region:
    parts = [int(p.strip()) for p in value.split(",")]
    if len(parts) != 4:
        raise ValueError("region must be 'x,y,width,height'")
    x, y, w, h = parts
    if w <= 0 or h <= 0:
        raise ValueError("region width and height must be positive")
    return (x, y, w, h)


@dataclass
class Settings:
    tutor_model: str = "claude-opus-5"
    ocr_model: str = "claude-opus-5"
    tutor_effort: str = "high"
    ocr_effort: str = "low"
    level: str = "beginner"

    ja_voice: str = "ja-JP-NanamiNeural"
    en_voice: str = "en-US-AriaNeural"
    ja_rate_slow: str = "-20%"
    ja_rate_normal: str = "+0%"
    en_rate: str = "+0%"

    region: Optional[Region] = None
    poll_interval: float = 0.5
    stability_frames: int = 2
    change_threshold: float = 0.02

    cache_dir: Path = field(default_factory=lambda: Path(".jptutor-cache"))
    max_queue: int = 3
    remember_lines: int = 200

    @classmethod
    def from_env(cls, env: Optional[dict] = None) -> "Settings":
        e = os.environ if env is None else env
        s = cls()
        s.tutor_model = e.get("JPTUTOR_TUTOR_MODEL", s.tutor_model)
        s.ocr_model = e.get("JPTUTOR_OCR_MODEL", s.ocr_model)
        s.tutor_effort = e.get("JPTUTOR_TUTOR_EFFORT", s.tutor_effort)
        s.ocr_effort = e.get("JPTUTOR_OCR_EFFORT", s.ocr_effort)
        s.level = e.get("JPTUTOR_LEVEL", s.level)
        s.ja_voice = e.get("JPTUTOR_JA_VOICE", s.ja_voice)
        s.en_voice = e.get("JPTUTOR_EN_VOICE", s.en_voice)
        if e.get("JPTUTOR_REGION"):
            s.region = parse_region(e["JPTUTOR_REGION"])
        s.poll_interval = float(e.get("JPTUTOR_POLL_INTERVAL", s.poll_interval))
        s.change_threshold = float(e.get("JPTUTOR_CHANGE_THRESHOLD", s.change_threshold))
        if e.get("JPTUTOR_CACHE_DIR"):
            s.cache_dir = Path(e["JPTUTOR_CACHE_DIR"])
        return s

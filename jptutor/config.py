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
    backend: str = "auto"  # auto | api | claude-code
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

    tts: str = "auto"  # edge | system | auto (edge, falling back to the system voice if it fails)
    prespeak: bool = True  # play the Japanese line as soon as OCR has it, while the lesson generates
    hotkeys: bool = True
    hotkey_spec: str = ""  # e.g. "skip=<f9>,pause=<f10>"
    auto_region: bool = False  # let the first OCR result pick the dialogue box
    repeat_skip_after: int = 3  # a known line is replayed until seen this many times, then skipped

    region: Optional[Region] = None
    poll_interval: float = 0.5
    stability_frames: int = 2
    change_threshold: float = 0.02

    overlay: bool = True
    overlay_geometry: Optional[Region] = None  # x,y,w,h; default bottom centre of the primary screen
    overlay_font_size: int = 34
    overlay_opacity: float = 0.88

    cache_ttl: str = "1h"  # prompt-cache TTL for the API backend: 5m | 1h
    knowledge_refresh: int = 6  # lessons between memory-summary snapshots (the snapshot is what gets cached)

    memory_path: Optional[Path] = field(default_factory=lambda: Path.home() / ".jptutor" / "memory.sqlite")
    repeat: str = "quick"  # what to do with a line taught in an earlier session: quick | skip | full

    cache_dir: Path = field(default_factory=lambda: Path(".jptutor-cache"))
    max_queue: int = 3
    remember_lines: int = 200

    @classmethod
    def from_env(cls, env: Optional[dict] = None) -> "Settings":
        e = os.environ if env is None else env
        s = cls()
        s.backend = e.get("JPTUTOR_BACKEND", s.backend)
        s.tutor_model = e.get("JPTUTOR_TUTOR_MODEL", s.tutor_model)
        s.ocr_model = e.get("JPTUTOR_OCR_MODEL", s.ocr_model)
        s.tutor_effort = e.get("JPTUTOR_TUTOR_EFFORT", s.tutor_effort)
        s.ocr_effort = e.get("JPTUTOR_OCR_EFFORT", s.ocr_effort)
        s.level = e.get("JPTUTOR_LEVEL", s.level)
        s.ja_voice = e.get("JPTUTOR_JA_VOICE", s.ja_voice)
        s.en_voice = e.get("JPTUTOR_EN_VOICE", s.en_voice)
        s.tts = e.get("JPTUTOR_TTS", s.tts)
        s.prespeak = e.get("JPTUTOR_PRESPEAK", "1") not in ("0", "false", "no", "off")
        s.hotkeys = e.get("JPTUTOR_HOTKEYS", "1") not in ("0", "false", "no", "off")
        s.hotkey_spec = e.get("JPTUTOR_HOTKEY_MAP", s.hotkey_spec)
        s.auto_region = e.get("JPTUTOR_AUTO_REGION", "0") in ("1", "true", "yes", "on")
        s.repeat_skip_after = int(e.get("JPTUTOR_REPEAT_SKIP_AFTER", s.repeat_skip_after))
        if e.get("JPTUTOR_REGION"):
            s.region = parse_region(e["JPTUTOR_REGION"])
        s.poll_interval = float(e.get("JPTUTOR_POLL_INTERVAL", s.poll_interval))
        s.change_threshold = float(e.get("JPTUTOR_CHANGE_THRESHOLD", s.change_threshold))
        s.overlay = e.get("JPTUTOR_OVERLAY", "1") not in ("0", "false", "no", "off")
        if e.get("JPTUTOR_OVERLAY_GEOMETRY"):
            s.overlay_geometry = parse_region(e["JPTUTOR_OVERLAY_GEOMETRY"])
        s.overlay_font_size = int(e.get("JPTUTOR_OVERLAY_FONT_SIZE", s.overlay_font_size))
        s.overlay_opacity = float(e.get("JPTUTOR_OVERLAY_OPACITY", s.overlay_opacity))
        s.cache_ttl = e.get("JPTUTOR_CACHE_TTL", s.cache_ttl)
        s.knowledge_refresh = int(e.get("JPTUTOR_KNOWLEDGE_REFRESH", s.knowledge_refresh))
        mem = e.get("JPTUTOR_MEMORY")
        if mem is not None:
            s.memory_path = None if mem in ("0", "", "off", "none") else Path(mem).expanduser()
        s.repeat = e.get("JPTUTOR_REPEAT", s.repeat)
        if e.get("JPTUTOR_CACHE_DIR"):
            s.cache_dir = Path(e["JPTUTOR_CACHE_DIR"])
        return s

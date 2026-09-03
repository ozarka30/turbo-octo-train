"""Text-to-speech backends and audio playback.

Default backend is edge-tts (Microsoft neural voices, free, no key needed).
Synthesised clips are cached on disk by content hash so repeated words are instant.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Optional, Protocol

from .config import Settings
from .script import Utterance

log = logging.getLogger(__name__)


class Speaker(Protocol):
    def speak(self, utterance: Utterance) -> None: ...

    def speak_all(self, script: Iterable[Utterance]) -> None: ...


class ConsoleSpeaker:
    """Prints instead of speaking. Used by --dry-run and tests."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.spoken = []

    def speak(self, utterance: Utterance) -> None:
        self.spoken.append(utterance)
        tag = f"{utterance.lang}{' slow' if utterance.slow else ''}"
        print(f"  [{tag}] {utterance.text}", file=self.out)

    def speak_all(self, script: Iterable[Utterance]) -> None:
        for u in script:
            self.speak(u)


def find_player() -> Optional[list]:
    """Return a command prefix that plays an mp3 file path appended to it."""
    system = platform.system()
    candidates = []
    if system == "Darwin":
        candidates.append(["afplay"])
    candidates += [
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
        ["mpg123", "-q"],
        ["mpv", "--no-video", "--really-quiet"],
    ]
    if system == "Windows":
        candidates.append(["powershell", "-c", "(New-Object Media.SoundPlayer $args[0]).PlaySync()"])
    for cmd in candidates:
        if shutil.which(cmd[0]):
            return cmd
    return None


def play_file(path: Path) -> None:
    try:
        import pygame  # type: ignore

        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.load(str(path))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.05)
        return
    except ImportError:
        pass
    cmd = find_player()
    if cmd is None:
        raise RuntimeError(
            "No audio player found. Install pygame (pip install pygame) or ffmpeg (ffplay) / mpg123 / mpv."
        )
    subprocess.run(cmd + [str(path)], check=False)


class EdgeSpeaker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache = settings.cache_dir / "tts"
        self.cache.mkdir(parents=True, exist_ok=True)

    def _voice_and_rate(self, u: Utterance):
        s = self.settings
        if u.lang == "ja":
            return s.ja_voice, (s.ja_rate_slow if u.slow else s.ja_rate_normal)
        return s.en_voice, s.en_rate

    def synth(self, u: Utterance) -> Path:
        voice, rate = self._voice_and_rate(u)
        key = hashlib.sha1(f"{voice}|{rate}|{u.text}".encode("utf-8")).hexdigest()
        path = self.cache / f"{key}.mp3"
        if path.exists() and path.stat().st_size > 0:
            return path
        import edge_tts

        async def run():
            await edge_tts.Communicate(u.text, voice, rate=rate).save(str(path))

        asyncio.run(run())
        return path

    def speak(self, u: Utterance) -> None:
        path = self.synth(u)
        play_file(path)
        if u.pause_after > 0:
            time.sleep(u.pause_after)

    def prepare(self, script: Iterable[Utterance]) -> None:
        """Synthesise every clip up front so playback (and the highlight) has no gaps."""
        for u in script:
            self.synth(u)

    def speak_all(self, script: Iterable[Utterance]) -> None:
        script = list(script)
        self.prepare(script)
        for u in script:
            self.speak(u)


def make_speaker(settings: Settings, backend: str = "edge") -> Speaker:
    if backend == "console":
        return ConsoleSpeaker()
    if backend == "edge":
        return EdgeSpeaker(settings)
    raise ValueError(f"unknown tts backend: {backend}")

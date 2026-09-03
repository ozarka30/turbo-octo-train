"""Text-to-speech backends and audio playback.

Default backend is edge-tts (Microsoft neural voices, free, no key needed).
Synthesised clips are cached on disk by content hash so repeated words are
instant. Playback goes through pygame, which is a hard dependency because it is
the one player that works the same on Windows, macOS, and Linux.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional, Protocol

from .config import Settings
from .errors import FatalError
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


# ----------------------------------------------------------------------------- playback
def find_player() -> Optional[list]:
    """Command prefix for an external mp3 player, used only when pygame is missing."""
    candidates = []
    if platform.system() == "Darwin":
        candidates.append(["afplay"])
    candidates += [
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
        ["mpg123", "-q"],
        ["mpv", "--no-video", "--really-quiet"],
    ]
    for cmd in candidates:
        if shutil.which(cmd[0]):
            return cmd
    return None


class Player:
    """Plays audio files; playback can be cut short by setting `stop`."""

    def __init__(self):
        self._pygame = None
        try:
            import pygame  # type: ignore

            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            self._pygame = pygame
        except ImportError:
            pass
        self._cmd = None if self._pygame else find_player()
        if self._pygame is None and self._cmd is None:
            raise FatalError("No audio player found. Run `pip install pygame` (or install ffmpeg / mpg123 / mpv).")
        self._proc: Optional[subprocess.Popen] = None

    def describe(self) -> str:
        return "pygame" if self._pygame else self._cmd[0]

    def play(self, path: Path, stop: Optional[threading.Event] = None) -> None:
        stop = stop or threading.Event()
        if self._pygame:
            pg = self._pygame
            if not pg.mixer.get_init():
                pg.mixer.init()
            pg.mixer.music.load(str(path))
            pg.mixer.music.play()
            while pg.mixer.music.get_busy():
                if stop.is_set():
                    pg.mixer.music.stop()
                    break
                time.sleep(0.03)
            pg.mixer.music.unload()
            return
        self._proc = subprocess.Popen(self._cmd + [str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while self._proc.poll() is None:
            if stop.is_set():
                self._proc.terminate()
                break
            time.sleep(0.03)
        self._proc = None


# ----------------------------------------------------------------------------- edge-tts
class EdgeSpeaker:
    def __init__(self, settings: Settings, player: Optional[Player] = None, *, concurrency: int = 4):
        self.settings = settings
        self.cache = settings.cache_dir / "tts"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.player = player or Player()
        self.concurrency = concurrency
        self.stop = threading.Event()  # set to cut the current clip short

    def _voice_and_rate(self, u: Utterance):
        s = self.settings
        if u.lang == "ja":
            return s.ja_voice, (s.ja_rate_slow if u.slow else s.ja_rate_normal)
        return s.en_voice, s.en_rate

    def _path(self, u: Utterance) -> Path:
        voice, rate = self._voice_and_rate(u)
        key = hashlib.sha1(f"{voice}|{rate}|{u.text}".encode("utf-8")).hexdigest()
        return self.cache / f"{key}.mp3"

    async def _synth_one(self, u: Utterance, sem: asyncio.Semaphore) -> Path:
        import edge_tts

        path = self._path(u)
        if path.exists() and path.stat().st_size > 0:
            return path
        voice, rate = self._voice_and_rate(u)
        tmp = path.with_suffix(".part")
        async with sem:
            await edge_tts.Communicate(u.text, voice, rate=rate).save(str(tmp))
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise RuntimeError(f"edge-tts produced no audio for {u.text[:30]!r}")
        os.replace(tmp, path)  # only a complete clip ever lands in the cache
        return path

    async def _synth_many(self, script: List[Utterance]) -> List[Path]:
        sem = asyncio.Semaphore(self.concurrency)
        return await asyncio.gather(*(self._synth_one(u, sem) for u in script))

    def synth(self, u: Utterance) -> Path:
        return self.prepare([u])[0]

    def prepare(self, script: Iterable[Utterance]) -> List[Path]:
        """Synthesise every clip up front, several at a time, so playback has no gaps."""
        script = list(script)
        if not script:
            return []
        try:
            return asyncio.run(self._synth_many(script))
        except Exception as e:
            raise RuntimeError(f"text-to-speech failed ({type(e).__name__}: {e}); is the network up?") from e

    def speak(self, u: Utterance) -> None:
        path = self.synth(u)
        self.player.play(path, self.stop)
        if u.pause_after > 0 and not self.stop.is_set():
            self.stop.wait(u.pause_after)

    def speak_all(self, script: Iterable[Utterance]) -> None:
        script = list(script)
        self.prepare(script)
        for u in script:
            if self.stop.is_set():
                break
            self.speak(u)


def make_speaker(settings: Settings, backend: str = "edge") -> Speaker:
    if backend == "console":
        return ConsoleSpeaker()
    if backend == "edge":
        return EdgeSpeaker(settings)
    raise ValueError(f"unknown tts backend: {backend}")

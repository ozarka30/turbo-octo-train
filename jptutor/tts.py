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
from typing import Callable, Iterable, List, Optional, Protocol

Abort = Callable[[], bool]


def _never() -> bool:
    return False

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

    def play(self, path: Path, abort: Abort = _never) -> None:
        if self._pygame:
            pg = self._pygame
            if not pg.mixer.get_init():
                pg.mixer.init()
            pg.mixer.music.load(str(path))
            pg.mixer.music.play()
            while pg.mixer.music.get_busy():
                if abort():
                    pg.mixer.music.stop()
                    break
                time.sleep(0.03)
            pg.mixer.music.unload()
            return
        run_interruptible(self._cmd + [str(path)], abort)


def run_interruptible(cmd: List[str], abort: Abort = _never, **popen_kw) -> int:
    """Run a command, killing it early if `abort()` turns true."""
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **popen_kw)
    while proc.poll() is None:
        if abort():
            proc.terminate()
            break
        time.sleep(0.03)
    return proc.returncode if proc.returncode is not None else -1


# ----------------------------------------------------------------------------- edge-tts
class EdgeSpeaker:
    def __init__(self, settings: Settings, player: Optional[Player] = None, *, concurrency: int = 4):
        self.settings = settings
        self.cache = settings.cache_dir / "tts"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.player = player or Player()
        self.concurrency = concurrency
        self.should_abort: Abort = _never  # cut the current clip short when this turns true

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
        self.player.play(path, self.should_abort)
        _pause(u.pause_after, self.should_abort)

    def speak_all(self, script: Iterable[Utterance]) -> None:
        script = list(script)
        self.prepare(script)
        for u in script:
            if self.should_abort():
                break
            self.speak(u)


def _pause(seconds: float, abort: Abort) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end and not abort():
        time.sleep(0.05)


# ----------------------------------------------------------------------------- system voices
class SystemSpeaker:
    """The operating system's own voices: `say` on macOS, SAPI on Windows, espeak-ng on Linux.

    Lower quality than edge-tts but works offline. Japanese needs a Japanese voice
    installed (macOS: Kyoko or Otoya; Windows: Haruka / Ayumi / Ichiro from Settings >
    Language > Japanese; Linux: espeak-ng ships one).
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.should_abort: Abort = _never
        self.system = platform.system()
        if self.system == "Darwin":
            if not shutil.which("say"):
                raise FatalError("macOS `say` not found")
        elif self.system == "Windows":
            if not shutil.which("powershell"):
                raise FatalError("PowerShell not found for the Windows voice")
        else:
            if not shutil.which("espeak-ng") and not shutil.which("espeak"):
                raise FatalError("Install espeak-ng for the offline voice (apt install espeak-ng)")
        self.ja_voice = getattr(settings, "system_ja_voice", "") or ("Kyoko" if self.system == "Darwin" else "")
        self.en_voice = getattr(settings, "system_en_voice", "") or ("Samantha" if self.system == "Darwin" else "")

    def describe(self) -> str:
        return {"Darwin": "macOS say", "Windows": "Windows SAPI"}.get(self.system, "espeak-ng")

    def speak(self, u: Utterance) -> None:
        rate_mult = 0.8 if (u.lang == "ja" and u.slow) else 1.0
        if self.system == "Darwin":
            voice = self.ja_voice if u.lang == "ja" else self.en_voice
            cmd = ["say", "-r", str(int(180 * rate_mult))]
            if voice:
                cmd += ["-v", voice]
            run_interruptible(cmd + [u.text], self.should_abort)
        elif self.system == "Windows":
            culture = "ja-JP" if u.lang == "ja" else "en-US"
            rate = -2 if rate_mult < 1 else 0
            text = u.text.replace("'", "''")
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$v = $s.GetInstalledVoices() | Where-Object {{ $_.VoiceInfo.Culture.Name -eq '{culture}' }} | Select-Object -First 1; "
                "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; "
                f"$s.Rate = {rate}; $s.Speak('{text}')"
            )
            run_interruptible(["powershell", "-NoProfile", "-Command", script], self.should_abort)
        else:
            exe = shutil.which("espeak-ng") or "espeak"
            voice = "ja" if u.lang == "ja" else "en-us"
            run_interruptible([exe, "-v", voice, "-s", str(int(150 * rate_mult)), u.text], self.should_abort)
        _pause(u.pause_after, self.should_abort)

    def prepare(self, script: Iterable[Utterance]) -> None:
        return None

    def speak_all(self, script: Iterable[Utterance]) -> None:
        for u in script:
            if self.should_abort():
                break
            self.speak(u)


class FallbackSpeaker:
    """Use the primary speaker; if synthesis fails, switch to the fallback for the session."""

    def __init__(self, primary, fallback, on_switch: Optional[Callable[[str], None]] = None):
        self.primary, self.fallback = primary, fallback
        self.active = primary
        self.on_switch = on_switch or (lambda msg: log.warning(msg))
        self._abort: Abort = _never

    @property
    def should_abort(self) -> Abort:
        return self._abort

    @should_abort.setter
    def should_abort(self, fn: Abort) -> None:
        self._abort = fn
        for spk in (self.primary, self.fallback):
            if hasattr(spk, "should_abort"):
                spk.should_abort = fn

    def _switch(self, err: Exception) -> None:
        if self.active is self.primary:
            self.active = self.fallback
            self.on_switch(f"{type(self.primary).__name__} failed ({err}); using {self.fallback.describe()} for the rest of the session")

    def prepare(self, script: Iterable[Utterance]) -> None:
        script = list(script)
        prep = getattr(self.active, "prepare", None)
        if prep is None:
            return
        try:
            prep(script)
        except FatalError:
            raise
        except Exception as e:
            self._switch(e)

    def speak(self, u: Utterance) -> None:
        try:
            self.active.speak(u)
        except FatalError:
            raise
        except Exception as e:
            self._switch(e)
            self.active.speak(u)

    def speak_all(self, script: Iterable[Utterance]) -> None:
        script = list(script)
        self.prepare(script)
        for u in script:
            if self._abort():
                break
            self.speak(u)


def make_speaker(settings: Settings, backend: str = "edge") -> Speaker:
    if backend == "console":
        return ConsoleSpeaker()
    if backend == "edge":
        return EdgeSpeaker(settings)
    if backend == "system":
        return SystemSpeaker(settings)
    if backend == "auto":
        edge = EdgeSpeaker(settings)
        try:
            system = SystemSpeaker(settings)
        except FatalError as e:
            log.info("no system voice for fallback: %s", e)
            return edge
        return FallbackSpeaker(edge, system)
    raise ValueError(f"unknown tts backend: {backend}")

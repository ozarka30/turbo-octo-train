"""Speech practice: the learner says the line, we check how close it was.

Pipeline: microphone -> faster-whisper (local, Japanese) -> kana via pykakasi
-> sequence diff against the target reading -> spoken feedback that names the
piece to listen to again and replays it. Everything runs on your machine; no
audio leaves it.

Optional dependencies (pip install "jptutor[speech]"): sounddevice,
faster-whisper, pykakasi. `available()` reports what is missing.
"""

from __future__ import annotations

import difflib
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Protocol, Tuple

from .lesson import Lesson
from .script import Utterance, find_span

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000


# ----------------------------------------------------------------------------- kana
_KATA_TO_HIRA = {i: i - 0x60 for i in range(0x30A1, 0x30F7)}
_STRIP = re.compile(r"[^ぁ-ゖー]")

_kakasi = None


def _converter():
    global _kakasi
    if _kakasi is None:
        import pykakasi

        _kakasi = pykakasi.kakasi()
    return _kakasi


def to_kana(text: str) -> str:
    """Any Japanese text -> hiragana only (no punctuation, spaces, or kanji)."""
    text = unicodedata.normalize("NFKC", text)
    hira = "".join(item["hira"] for item in _converter().convert(text))
    hira = hira.translate(_KATA_TO_HIRA)
    return _STRIP.sub("", hira)


def normalize_kana(kana: str) -> str:
    kana = unicodedata.normalize("NFKC", kana).translate(_KATA_TO_HIRA)
    return _STRIP.sub("", kana)


# ----------------------------------------------------------------------------- scoring
@dataclass
class Attempt:
    heard: str  # what the recogniser wrote (kanji mix)
    heard_kana: str
    target_kana: str
    score: float  # 0..1
    misses: List[Tuple[str, str]] = field(default_factory=list)  # (expected kana, heard kana)

    @property
    def tier(self) -> str:
        """great = every mora matched; close = mostly there, one piece to fix; off = try again."""
        if not self.heard_kana:
            return "silent"
        if not self.misses:
            return "great"
        if self.score >= 0.7:
            return "close"
        return "off"


def score_attempt(target_japanese: str, target_reading: str, heard: str) -> Attempt:
    """Compare what was heard with the target. The target is tried both as the
    lesson's reading and as pykakasi's own reading of the kanji, so a converter
    quirk on one side does not count against the learner."""
    heard_kana = to_kana(heard)
    candidates = [normalize_kana(target_reading)] if target_reading else []
    candidates.append(to_kana(target_japanese))
    candidates = [c for c in candidates if c]
    best: Optional[Attempt] = None
    for target in candidates:
        sm = difflib.SequenceMatcher(None, target, heard_kana, autojunk=False)
        ratio = sm.ratio()
        misses = [(target[i1:i2], heard_kana[j1:j2]) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]
        attempt = Attempt(heard=heard, heard_kana=heard_kana, target_kana=target, score=ratio, misses=misses)
        if best is None or attempt.score > best.score:
            best = attempt
    return best or Attempt(heard=heard, heard_kana=heard_kana, target_kana="", score=0.0)


def piece_for(lesson: Lesson, expected_kana: str) -> Optional[Tuple[str, str, str]]:
    """The chunk (japanese, reading, meaning) that contains the missed kana, if any."""
    if not expected_kana:
        return None
    for c in lesson.chunks:
        r = normalize_kana(c.reading) or to_kana(c.japanese)
        if expected_kana in r or (len(expected_kana) >= 2 and r and r in expected_kana):
            return (c.japanese, c.reading, c.meaning)
    return None


def feedback(lesson: Lesson, attempt: Attempt) -> List[Utterance]:
    """Spoken feedback, Paul Noble style: short, and it points at the piece to fix."""
    whole = (0, len(lesson.japanese))
    tier = attempt.tier
    if tier == "silent":
        return [Utterance("en", "I did not catch anything. Try once more after the beep next time.", span=whole)]
    if tier == "great":
        return [Utterance("en", "Very close. That is it.", span=whole, pause_after=0.5)]
    out: List[Utterance] = []
    biggest = max(attempt.misses, key=lambda m: len(m[0]), default=("", ""))
    piece = piece_for(lesson, biggest[0])
    if tier == "close":
        out.append(Utterance("en", "Close.", pause_after=0.2, span=whole))
    else:
        out.append(Utterance("en", "Not quite.", pause_after=0.2, span=whole))
    if piece:
        jp, reading, meaning = piece
        span = find_span(lesson.japanese, jp)
        out.append(Utterance("en", f"Listen to the piece that means {meaning}.", span=span, reading=reading, pause_after=0.2))
        out.append(Utterance("ja", reading or jp, slow=True, span=span, reading=reading, pause_after=0.5))
    out.append(Utterance("en", "The whole line once more.", pause_after=0.2, span=whole))
    out.append(Utterance("ja", lesson.japanese, slow=True, span=whole, pause_after=0.6))
    return out


# ----------------------------------------------------------------------------- audio in
class Recorder(Protocol):
    def record(self, *, max_seconds: float, silence_seconds: float, wait_seconds: float): ...


class MicRecorder:
    """Records from the default microphone until the learner stops talking."""

    def __init__(self, threshold: float = 0.012, device=None):
        import numpy as np  # noqa: F401
        import sounddevice as sd

        self.sd = sd
        self.threshold = threshold
        self.device = device

    def record(self, *, max_seconds: float = 8.0, silence_seconds: float = 0.9, wait_seconds: float = 4.0):
        import numpy as np

        sd = self.sd
        block = int(SAMPLE_RATE * 0.05)
        chunks: List[np.ndarray] = []
        started = False
        quiet = 0.0
        t0 = time.monotonic()
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=block, device=self.device) as stream:
            while True:
                data, _ = stream.read(block)
                mono = data[:, 0]
                rms = float(np.sqrt(np.mean(mono ** 2)))
                elapsed = time.monotonic() - t0
                if not started:
                    if rms > self.threshold:
                        started = True
                        chunks.append(mono.copy())
                    elif elapsed > wait_seconds:
                        return np.zeros(0, dtype="float32")
                    continue
                chunks.append(mono.copy())
                quiet = quiet + 0.05 if rms < self.threshold else 0.0
                if quiet >= silence_seconds or elapsed > max_seconds:
                    break
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype="float32")


class Transcriber(Protocol):
    def transcribe(self, audio) -> str: ...


class WhisperTranscriber:
    """faster-whisper, loaded on first use (the model downloads once, ~500 MB for `small`)."""

    def __init__(self, model_size: str = "small", device: str = "auto", compute_type: str = "int8"):
        self.model_size, self.device, self.compute_type = model_size, device, compute_type
        self._model = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                log.info("loading whisper model %s", self.model_size)
                self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)

    def transcribe(self, audio) -> str:
        if audio is None or len(audio) == 0:
            return ""
        self.load()
        segments, _ = self._model.transcribe(audio, language="ja", beam_size=5, vad_filter=True)
        return "".join(seg.text for seg in segments).strip()


def available() -> Tuple[bool, str]:
    missing = []
    for mod in ("sounddevice", "faster_whisper", "pykakasi"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if missing:
        return False, "missing " + ", ".join(missing) + ' (pip install "jptutor[speech]")'
    return True, "ok"


# ----------------------------------------------------------------------------- runner
class Practice:
    """Runs one practice round for a lesson, talking through the given speaker/display."""

    def __init__(self, recorder: Recorder, transcriber: Transcriber, *, max_seconds: float = 8.0, silence_seconds: float = 0.9, wait_seconds: float = 4.0):
        self.recorder = recorder
        self.transcriber = transcriber
        self.max_seconds, self.silence_seconds, self.wait_seconds = max_seconds, silence_seconds, wait_seconds
        self.attempts: List[Attempt] = []

    @classmethod
    def default(cls, whisper_model: str = "small") -> "Practice":
        return cls(MicRecorder(), WhisperTranscriber(whisper_model))

    def run(self, lesson: Lesson, speaker, display, *, warm_up: Optional[Callable[[], None]] = None) -> Optional[Attempt]:
        whole = (0, len(lesson.japanese))
        display.show_lesson(lesson)
        speaker.speak(Utterance("en", "Your turn. Say the whole line.", span=whole, pause_after=0.1))
        display.show_practice("", None, listening=True)
        if warm_up:
            warm_up()
        audio = self.recorder.record(max_seconds=self.max_seconds, silence_seconds=self.silence_seconds, wait_seconds=self.wait_seconds)
        heard = self.transcriber.transcribe(audio)
        attempt = score_attempt(lesson.japanese, lesson.reading, heard)
        self.attempts.append(attempt)
        display.show_practice(attempt.heard_kana, attempt.score, listening=False)
        log.info("practice: heard %r (%s) score %.2f", heard, attempt.heard_kana, attempt.score)
        try:
            speaker.speak_all(feedback(lesson, attempt))
        finally:
            display.finish()
        return attempt

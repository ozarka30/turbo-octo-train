"""Orchestration: frame -> OCR -> dedupe -> lesson -> speech."""

from __future__ import annotations

import logging
import queue
import threading
import unicodedata
from collections import OrderedDict
from typing import Iterable, List, Optional

from PIL import Image

from .claude_client import TutorBackend
from .config import Settings
from .lesson import Lesson, OcrLine, contains_japanese
from .script import build_script
from .tts import Speaker

log = logging.getLogger(__name__)

TEACHABLE_KINDS = ("dialogue", "narration")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return "".join(text.split())


class SeenLines:
    """Bounded memory of lines already taught, so re-rendered text is not re-taught."""

    def __init__(self, capacity: int = 200):
        self.capacity = capacity
        self._seen: "OrderedDict[str, None]" = OrderedDict()

    def add(self, text: str) -> bool:
        """Record the line. Returns True if it was new."""
        key = normalize(text)
        if key in self._seen:
            self._seen.move_to_end(key)
            return False
        self._seen[key] = None
        while len(self._seen) > self.capacity:
            self._seen.popitem(last=False)
        return True

    def __contains__(self, text: str) -> bool:
        return normalize(text) in self._seen


def pick_lines(lines: Iterable[OcrLine], min_chars: int = 2) -> List[OcrLine]:
    """Keep story text that is worth a lesson; drop HUD, menus, and non-Japanese."""
    out = []
    for line in lines:
        text = line.text.strip()
        if line.kind not in TEACHABLE_KINDS:
            continue
        if not contains_japanese(text) or len(normalize(text)) < min_chars:
            continue
        out.append(line)
    return out


class TutorPipeline:
    def __init__(self, tutor: TutorBackend, speaker: Speaker, settings: Settings, *, context: str = "", full_breakdown: bool = True):
        self.tutor = tutor
        self.speaker = speaker
        self.settings = settings
        self.context = context
        self.full_breakdown = full_breakdown
        self.seen = SeenLines(settings.remember_lines)
        self.history: List[str] = []
        self.lessons: List[Lesson] = []

    # -- single-line path ----------------------------------------------------
    def teach_text(self, japanese: str, speaker: str = "") -> Optional[Lesson]:
        if not self.seen.add(japanese):
            log.info("already taught, skipping: %s", japanese)
            return None
        lesson = self.tutor.teach(japanese, speaker=speaker, context=self.context, history=self.history[-40:])
        self.lessons.append(lesson)
        self.history.extend(f"{c.japanese} = {c.meaning}" for c in lesson.chunks)
        self.speaker.speak_all(build_script(lesson, full_breakdown=self.full_breakdown))
        return lesson

    # -- screenshot path -----------------------------------------------------
    def handle_frame(self, frame: Image.Image) -> List[Lesson]:
        result = self.tutor.ocr(frame)
        taught = []
        for line in pick_lines(result.lines):
            lesson = self.teach_text(line.text, speaker=line.speaker)
            if lesson:
                taught.append(lesson)
        return taught


class FrameWorker:
    """Runs the pipeline on a background thread fed by a bounded queue.

    The capture loop keeps grabbing while a lesson is being spoken; if lessons
    pile up faster than they can be spoken, the oldest queued frames are dropped.
    """

    def __init__(self, pipeline: TutorPipeline, max_queue: int = 3):
        self.pipeline = pipeline
        self.q: "queue.Queue[Optional[Image.Image]]" = queue.Queue(maxsize=max_queue)
        self._thread = threading.Thread(target=self._run, name="jptutor-worker", daemon=True)
        self.dropped = 0

    def start(self) -> "FrameWorker":
        self._thread.start()
        return self

    def submit(self, frame: Image.Image) -> None:
        try:
            self.q.put_nowait(frame)
        except queue.Full:
            try:
                self.q.get_nowait()
                self.dropped += 1
            except queue.Empty:
                pass
            self.q.put_nowait(frame)

    def stop(self) -> None:
        self.q.put(None)
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while True:
            frame = self.q.get()
            if frame is None:
                return
            try:
                self.pipeline.handle_frame(frame)
            except Exception:  # keep the loop alive across API hiccups
                log.exception("failed to process frame")

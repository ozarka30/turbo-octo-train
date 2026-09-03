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
from .display import Display, DisplaySpeaker, NullDisplay
from .memory import Memory
from .lesson import Lesson, OcrLine, contains_japanese, split_sentences
from .script import build_script
from .tts import Speaker

log = logging.getLogger(__name__)

TEACHABLE_KINDS = ("dialogue", "narration", "choice")


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
        if line.kind not in TEACHABLE_KINDS or not line.complete:
            continue
        if not contains_japanese(text) or len(normalize(text)) < min_chars:
            continue
        out.append(line)
    return out


class TutorPipeline:
    def __init__(
        self,
        tutor: TutorBackend,
        speaker: Speaker,
        settings: Settings,
        *,
        context: str = "",
        full_breakdown: bool = True,
        display: Optional[Display] = None,
        memory: Optional[Memory] = None,
    ):
        self.tutor = tutor
        self.memory = memory
        self.repeat = settings.repeat
        self.display = display or NullDisplay()
        self.speaker = DisplaySpeaker(speaker, self.display)
        self.settings = settings
        self.context = context
        self.full_breakdown = full_breakdown
        self.seen = SeenLines(settings.remember_lines)
        self.history: List[str] = []  # this session only, used when there is no memory
        self.lessons: List[Lesson] = []
        self.replayed = 0
        # The memory summary is snapshotted every few lessons so the cached prompt prefix
        # stays byte-identical in between; lessons since the snapshot go in as a small delta.
        self._snapshot: Optional[str] = None
        self._snapshot_at = 0
        self._delta: List[str] = []

    # -- single-line path ----------------------------------------------------
    def teach_text(self, japanese: str, speaker: str = "", full_line: str = "") -> Optional[Lesson]:
        """Teach one sentence. Returns None if it was already taught this session."""
        if not self.seen.add(japanese):
            log.info("already taught, skipping: %s", japanese)
            return None

        # A line from an earlier session: replay from memory or skip, no Claude call.
        stored = self.memory.lookup_sentence(japanese) if self.memory else None
        if stored is not None and self.repeat != "full":
            if self.repeat == "skip":
                log.info("known line, skipping: %s", japanese)
                self.memory.touch_sentence(japanese)
                return None
            log.info("known line (seen %d times), quick replay: %s", stored.times_seen, japanese)
            self.memory.touch_sentence(japanese)
            self.replayed += 1
            self._speak(stored.lesson, full_breakdown=False)
            return stored.lesson

        knowledge, recent = self._knowledge()
        lesson = self.tutor.teach(
            japanese, speaker=speaker, context=self.context, full_line=full_line, knowledge=knowledge, recent=recent
        )
        self.lessons.append(lesson)
        if self.memory:
            self.memory.record_lesson(lesson, game=self.context, speaker=speaker)
        line = f"{lesson.japanese} = {lesson.english}; pieces: " + ", ".join(f"{c.japanese} ({c.meaning})" for c in lesson.chunks)
        self._delta.append(line)
        self.history.append(line)
        self._speak(lesson, full_breakdown=self.full_breakdown)
        return lesson

    def _knowledge(self):
        """(stable snapshot for the cached prompt, delta since the snapshot)."""
        if not self.memory:
            return "", "\n".join(self.history[-40:])
        if self._snapshot is None or len(self.lessons) - self._snapshot_at >= max(1, self.settings.knowledge_refresh):
            self._snapshot = self.memory.summary().render()
            self._snapshot_at = len(self.lessons)
            self._delta = []
        return self._snapshot, "\n".join(self._delta)

    def _speak(self, lesson: Lesson, *, full_breakdown: bool) -> None:
        self.display.show_lesson(lesson)
        try:
            self.speaker.speak_all(build_script(lesson, full_breakdown=full_breakdown))
        finally:
            self.display.finish()

    def teach_line(self, text: str, speaker: str = "") -> List[Lesson]:
        """Teach a whole dialogue box, one sentence at a time, with the box as context."""
        sentences = split_sentences(text)
        if len(sentences) == 1:
            lesson = self.teach_text(sentences[0], speaker=speaker)
            return [lesson] if lesson else []
        if not self.seen.add(text):
            return []
        taught = []
        for sentence in sentences:
            if not contains_japanese(sentence):
                continue
            lesson = self.teach_text(sentence, speaker=speaker, full_line=text)
            if lesson:
                taught.append(lesson)
        return taught

    # -- screenshot path -----------------------------------------------------
    def handle_frame(self, frame: Image.Image) -> List[Lesson]:
        result = self.tutor.ocr(frame)
        taught = []
        for line in pick_lines(result.lines):
            taught.extend(self.teach_line(line.text, speaker=line.speaker))
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

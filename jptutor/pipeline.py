"""Orchestration: frame -> OCR -> dedupe -> lesson -> speech."""

from __future__ import annotations

import logging
import queue
import threading
import unicodedata
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, List, Optional, Tuple

from PIL import Image

from .claude_client import TutorBackend
from .config import Settings
from .controls import Controls
from .display import Display, DisplaySpeaker, NullDisplay
from .errors import FatalError
from .lesson import Lesson, OcrLine, contains_japanese, split_sentences
from .memory import Memory
from .script import build_script, intro_utterance
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
        stop: Optional[threading.Event] = None,
        controls: Optional[Controls] = None,
    ):
        self.tutor = tutor
        self.memory = memory
        self.repeat = settings.repeat
        self.display = display or NullDisplay()
        self.stop = stop or threading.Event()
        self.controls = controls or Controls()
        self.speaker = DisplaySpeaker(speaker, self.display, stop=self.stop, controls=self.controls)
        self.last_lesson: Optional[Lesson] = None
        self.last_box: Optional[Tuple[List[float], Tuple[int, int]]] = None  # (fractions, frame size)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jptutor-claude")
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
        """Teach one sentence. Returns None if it was skipped.

        The sentence is only marked as seen (and stored in memory) once the
        lesson has been spoken, so a failed API or speech call leaves it eligible
        for a retry rather than silently lost.
        """
        if japanese in self.seen:
            log.info("already taught, skipping: %s", japanese)
            return None

        # A line from an earlier session: replay from memory or skip, no Claude call.
        stored = self.memory.lookup_sentence(japanese) if self.memory else None
        if stored is not None and self.repeat != "full":
            skip_after = max(1, self.settings.repeat_skip_after)
            if self.repeat == "skip" or stored.times_seen >= skip_after:
                log.info("known line (seen %d times), skipping: %s", stored.times_seen, japanese)
                self.seen.add(japanese)
                return None
            log.info("known line (seen %d times), quick replay: %s", stored.times_seen, japanese)
            self.controls.begin_lesson()
            self._speak(stored.lesson, full_breakdown=False)
            self.seen.add(japanese)
            self.memory.touch_sentence(japanese)
            self.replayed += 1
            self.last_lesson = stored.lesson
            return stored.lesson

        knowledge, recent = self._knowledge()
        self.controls.begin_lesson()
        prespeak = self.settings.prespeak and not self.stop.is_set()
        if prespeak:
            # Start the lesson request, and read the line aloud while Claude works on it.
            future = self._executor.submit(
                self.tutor.teach, japanese, speaker=speaker, context=self.context, full_line=full_line,
                knowledge=knowledge, recent=recent,
            )
            self.display.show_sentence(japanese)
            try:
                self.speaker.speak(intro_utterance(japanese))
            except Exception:
                log.exception("could not play the line early; continuing with the lesson")
            lesson = future.result()
        else:
            lesson = self.tutor.teach(
                japanese, speaker=speaker, context=self.context, full_line=full_line, knowledge=knowledge, recent=recent
            )
        self._speak(lesson, full_breakdown=self.full_breakdown, skip_intro=prespeak)
        self._remember(lesson, japanese, speaker)
        return lesson

    def repeat_last(self, *, full_breakdown: bool = True) -> bool:
        """Speak the most recent lesson again (hotkey)."""
        if self.last_lesson is None:
            return False
        self.controls.begin_lesson()
        self._speak(self.last_lesson, full_breakdown=full_breakdown)
        return True

    def _remember(self, lesson: Lesson, japanese: str, speaker: str) -> None:
        self.seen.add(japanese)
        self.lessons.append(lesson)
        self.last_lesson = lesson
        if self.memory:
            self.memory.record_lesson(lesson, game=self.context, speaker=speaker, key_text=japanese)
        line = f"{lesson.japanese} = {lesson.english}; pieces: " + ", ".join(f"{c.japanese} ({c.meaning})" for c in lesson.chunks)
        self._delta.append(line)
        self.history.append(line)

    def _knowledge(self):
        """(stable snapshot for the cached prompt, delta since the snapshot)."""
        if not self.memory:
            return "", "\n".join(self.history[-40:])
        if self._snapshot is None or len(self.lessons) - self._snapshot_at >= max(1, self.settings.knowledge_refresh):
            self._snapshot = self.memory.summary().render()
            self._snapshot_at = len(self.lessons)
            self._delta = []
        return self._snapshot, "\n".join(self._delta)

    def _speak(self, lesson: Lesson, *, full_breakdown: bool, skip_intro: bool = False) -> None:
        self.display.show_lesson(lesson)
        try:
            self.speaker.speak_all(build_script(lesson, full_breakdown=full_breakdown, skip_intro=skip_intro))
        finally:
            self.display.finish()
            self.controls.skip.clear()

    def teach_line(self, text: str, speaker: str = "") -> List[Lesson]:
        """Teach a whole dialogue box, one sentence at a time, with the box as context.

        One sentence failing (API hiccup, speech error) is logged and the next is
        still taught; fatal errors propagate.
        """
        sentences = split_sentences(text)
        if len(sentences) == 1:
            lesson = self.teach_text(sentences[0], speaker=speaker)
            return [lesson] if lesson else []
        if text in self.seen:
            return []
        taught = []
        for sentence in sentences:
            if self.stop.is_set():
                break
            if not contains_japanese(sentence):
                continue
            try:
                lesson = self.teach_text(sentence, speaker=speaker, full_line=text)
            except FatalError:
                raise
            except Exception:
                log.exception("failed to teach %r", sentence)
                self.display.show_error(f"Could not teach: {sentence}")
                continue
            if lesson:
                taught.append(lesson)
        if not self.stop.is_set():
            self.seen.add(text)
        return taught

    # -- screenshot path -----------------------------------------------------
    def handle_frame(self, frame: Image.Image) -> List[Lesson]:
        result = self.tutor.ocr(frame)
        if len(result.dialogue_box) == 4:
            self.last_box = (list(result.dialogue_box), frame.size)
        taught = []
        for line in pick_lines(result.lines):
            if self.stop.is_set():
                break
            taught.extend(self.teach_line(line.text, speaker=line.speaker))
        return taught


def box_to_region(box: List[float], frame_size: Tuple[int, int], region: Tuple[int, int, int, int], margin: float = 0.03) -> Tuple[int, int, int, int]:
    """Convert a dialogue box given as image fractions into absolute screen coordinates
    within `region` (the area the frame was grabbed from), with a little margin."""
    fx0, fy0, fx1, fy1 = box
    fx0, fy0 = max(0.0, fx0 - margin), max(0.0, fy0 - margin)
    fx1, fy1 = min(1.0, fx1 + margin), min(1.0, fy1 + margin)
    rx, ry, rw, rh = region
    fw, fh = frame_size
    sx, sy = rw / fw, rh / fh  # frame pixels -> screen pixels (1 unless the frame was scaled)
    x = rx + int(fx0 * fw * sx)
    y = ry + int(fy0 * fh * sy)
    w = int((fx1 - fx0) * fw * sx)
    h = int((fy1 - fy0) * fh * sy)
    return (x, y, max(w, 8), max(h, 8))


class FrameWorker:
    """Runs the pipeline on a background thread fed by a bounded queue.

    The capture loop keeps grabbing while a lesson is being spoken; if lessons
    pile up faster than they can be spoken, the oldest queued frames are dropped.
    Callables can be queued too (repeat, practice). A FatalError from the pipeline
    stops the session and is re-raised by `stop()`.
    """

    def __init__(self, pipeline: TutorPipeline, max_queue: int = 3):
        self.pipeline = pipeline
        self.q: "queue.Queue[Optional[object]]" = queue.Queue(maxsize=max_queue)
        self._thread = threading.Thread(target=self._run, name="jptutor-worker", daemon=True)
        self.dropped = 0
        self.error: Optional[BaseException] = None
        self.stop_event = pipeline.stop

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

    def submit_task(self, fn: Callable[[], None]) -> None:
        """Queue a control action (repeat, practice) to run on the worker thread."""
        self.submit(fn)  # type: ignore[arg-type]

    def stop(self, timeout: float = 10.0) -> None:
        """Ask the worker to finish: drop queued frames, cut the current clip, join."""
        self.stop_event.set()
        while True:  # drain so the sentinel is next
            try:
                self.q.get_nowait()
                self.dropped += 1
            except queue.Empty:
                break
        try:
            self.q.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=timeout)
        if self.error is not None:
            raise self.error

    def _run(self) -> None:
        while not self.stop_event.is_set():
            item = self.q.get()
            if item is None:
                return
            try:
                if callable(item):
                    item()
                else:
                    self.pipeline.handle_frame(item)
            except FatalError as e:
                log.error("stopping: %s", e)
                self.error = e
                self.pipeline.display.show_error(str(e))
                self.stop_event.set()
                return
            except Exception:  # keep the loop alive across API hiccups
                log.exception("failed to process frame")
                self.pipeline.display.show_error("That line failed, carrying on.")

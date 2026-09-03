"""Displays that follow the lesson: which piece of the sentence is being read or explained.

`Display` is the interface the pipeline and speaker talk to. `ConsoleDisplay`
prints the sentence with the current piece bracketed; `Overlay` (in overlay.py)
draws an always-on-top window over the game.
"""

from __future__ import annotations

import sys
import threading
from typing import Iterable, Optional, Protocol

from .lesson import Lesson
from .script import Utterance, mark_span


class Display(Protocol):
    def show_sentence(self, japanese: str) -> None: ...

    def show_lesson(self, lesson: Lesson) -> None: ...

    def on_utterance(self, utterance: Utterance) -> None: ...

    def finish(self) -> None: ...

    def show_error(self, message: str) -> None: ...


class ConsoleDisplay:
    """Text rendering of the highlight, used by --dry-run and tests."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.sentence = ""
        self.frames = []  # (marked sentence, reading, caption) per utterance
        self.errors = []

    def show_sentence(self, japanese: str) -> None:
        self.sentence = japanese
        print(f"  ┌ {japanese}", file=self.out)

    def show_lesson(self, lesson: Lesson) -> None:
        if self.sentence != lesson.japanese:
            self.show_sentence(lesson.japanese)

    def on_utterance(self, u: Utterance) -> None:
        marked = mark_span(self.sentence, u.span)
        caption = u.text if u.lang == "en" else ""
        self.frames.append((marked, u.reading, caption))
        piece = f"  {u.reading}" if u.reading and u.span else ""
        print(f"  │ {marked}{piece}", file=self.out)

    def finish(self) -> None:
        self.sentence = ""
        print("  └", file=self.out)

    def show_error(self, message: str) -> None:
        self.errors.append(message)
        print(f"  !! {message}", file=self.out)


class NullDisplay:
    def show_sentence(self, japanese: str) -> None: ...

    def show_lesson(self, lesson: Lesson) -> None: ...

    def on_utterance(self, utterance: Utterance) -> None: ...

    def finish(self) -> None: ...

    def show_error(self, message: str) -> None: ...


class DisplaySpeaker:
    """Wraps a Speaker so the display is updated right before each utterance is played,
    playback stops promptly when `stop` is set, and the controls' skip/pause are honoured."""

    def __init__(self, speaker, display: Optional[Display], stop: Optional[threading.Event] = None, controls=None):
        from .controls import Controls

        self.speaker = speaker
        self.display = display or NullDisplay()
        self.stop = stop or threading.Event()
        self.controls = controls or Controls()
        if hasattr(speaker, "should_abort"):
            speaker.should_abort = self.aborted  # the audio player polls this mid-clip

    def aborted(self) -> bool:
        return self.stop.is_set() or self.controls.skip.is_set()

    def speak(self, u: Utterance) -> None:
        self.controls.wait_if_paused(self.stop)
        if self.aborted():
            return
        self.display.on_utterance(u)
        self.speaker.speak(u)

    def speak_all(self, script: Iterable[Utterance]) -> None:
        script = list(script)
        prepare = getattr(self.speaker, "prepare", None)
        if prepare:
            prepare(script)  # synthesise everything first so highlights and audio stay in step
        for u in script:
            if self.aborted():
                break
            self.speak(u)

    @property
    def spoken(self):
        return getattr(self.speaker, "spoken", [])

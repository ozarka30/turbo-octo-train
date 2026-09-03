"""Displays that follow the lesson: which piece of the sentence is being read or explained.

`Display` is the interface the pipeline and speaker talk to. `ConsoleDisplay`
prints the sentence with the current piece bracketed; `Overlay` (in overlay.py)
draws an always-on-top window over the game.
"""

from __future__ import annotations

import sys
from typing import Iterable, Optional, Protocol

from .lesson import Lesson
from .script import Utterance, mark_span


class Display(Protocol):
    def show_lesson(self, lesson: Lesson) -> None: ...

    def on_utterance(self, utterance: Utterance) -> None: ...

    def finish(self) -> None: ...


class ConsoleDisplay:
    """Text rendering of the highlight, used by --dry-run and tests."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.sentence = ""
        self.frames = []  # (marked sentence, reading, caption) per utterance

    def show_lesson(self, lesson: Lesson) -> None:
        self.sentence = lesson.japanese
        print(f"  ┌ {lesson.japanese}", file=self.out)

    def on_utterance(self, u: Utterance) -> None:
        marked = mark_span(self.sentence, u.span)
        caption = u.text if u.lang == "en" else ""
        self.frames.append((marked, u.reading, caption))
        piece = f"  {u.reading}" if u.reading and u.span else ""
        print(f"  │ {marked}{piece}", file=self.out)

    def finish(self) -> None:
        print("  └", file=self.out)


class NullDisplay:
    def show_lesson(self, lesson: Lesson) -> None: ...

    def on_utterance(self, utterance: Utterance) -> None: ...

    def finish(self) -> None: ...


class DisplaySpeaker:
    """Wraps a Speaker so the display is updated right before each utterance is played."""

    def __init__(self, speaker, display: Optional[Display]):
        self.speaker = speaker
        self.display = display or NullDisplay()

    def speak(self, u: Utterance) -> None:
        self.display.on_utterance(u)
        self.speaker.speak(u)

    def speak_all(self, script: Iterable[Utterance]) -> None:
        script = list(script)
        prepare = getattr(self.speaker, "prepare", None)
        if prepare:
            prepare(script)  # synthesise everything first so highlights and audio stay in step
        for u in script:
            self.speak(u)

    @property
    def spoken(self):
        return getattr(self.speaker, "spoken", [])

"""Turn a Lesson into an ordered list of spoken utterances.

This is the "Paul Noble" pacing layer: Japanese first, then English, then the
pieces, then the learner rebuilds the sentence with thinking pauses, then the
whole line once more.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal

from .lesson import Lesson

Lang = Literal["ja", "en"]


@dataclass(frozen=True)
class Utterance:
    lang: Lang
    text: str
    slow: bool = False
    pause_after: float = 0.3  # seconds of silence after this utterance


def build_script(lesson: Lesson, *, quiz_pause: float = 2.0, full_breakdown: bool = True) -> List[Utterance]:
    """Return the spoken sequence for one in-game line."""
    s: List[Utterance] = []

    # 1. Hear it: the line in Japanese, slowly, then at speed.
    s.append(Utterance("ja", lesson.japanese, slow=True, pause_after=0.6))
    # 2. Understand it: English translation, plus the register when it matters.
    s.append(Utterance("en", lesson.english, pause_after=0.6))
    if full_breakdown and lesson.tone.strip():
        s.append(Utterance("en", lesson.tone.strip(), pause_after=0.5))

    if not full_breakdown:
        s.append(Utterance("ja", lesson.japanese, pause_after=0.5))
        return s

    # 3. Take it apart.
    s.append(Utterance("en", "Let's break that down.", pause_after=0.3))
    for chunk in lesson.chunks:
        s.append(Utterance("ja", chunk.reading or chunk.japanese, slow=True, pause_after=0.2))
        line = chunk.meaning.strip().rstrip(".")
        if chunk.note.strip():
            line = f"{line}. {chunk.note.strip()}"
        s.append(Utterance("en", line, pause_after=0.4))

    # 4. Word order, in one breath.
    if lesson.literal.strip():
        s.append(Utterance("en", f"So, piece by piece, it reads: {lesson.literal.strip()}", pause_after=0.5))

    # 5. Rebuild it: question, thinking pause, answer. The answer is spoken from
    # the kanji text: the Japanese voice parses mixed text better than bare kana.
    for step in lesson.build_up:
        s.append(Utterance("en", step.prompt_en, pause_after=quiz_pause))
        s.append(Utterance("ja", step.japanese or step.reading, pause_after=0.6))

    # 6. The takeaway.
    if lesson.pattern.strip():
        s.append(Utterance("en", lesson.pattern.strip(), pause_after=0.5))

    # 7. Once more, at speed, with meaning.
    s.append(Utterance("en", "Once more.", pause_after=0.2))
    s.append(Utterance("ja", lesson.japanese, pause_after=0.4))
    s.append(Utterance("en", lesson.english, pause_after=0.8))
    return s


def script_as_text(script: List[Utterance]) -> str:
    return "\n".join(f"[{u.lang}{' slow' if u.slow else ''}] {u.text}" for u in script)

"""Turn a Lesson into an ordered list of spoken utterances.

This is the "Paul Noble" pacing layer: Japanese first, then English, then the
pieces, then the learner rebuilds the sentence with thinking pauses, then the
whole line once more. Each utterance also carries the span of the sentence it
is about, so a display can highlight the piece being read or explained.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple

from .lesson import Lesson

Lang = Literal["ja", "en"]
Span = Tuple[int, int]  # [start, end) character range in lesson.japanese

_TRAILING_PUNCT = re.compile(r"[。！？!?、」』）)\s]+$")
_LEADING_PUNCT = re.compile(r"^[「『（(\s]+")


@dataclass(frozen=True)
class Utterance:
    lang: Lang
    text: str
    slow: bool = False
    pause_after: float = 0.3  # seconds of silence after this utterance
    span: Optional[Span] = None  # part of the sentence this is reading or talking about
    reading: str = ""  # kana for the highlighted piece, when it is a single chunk


def find_span(sentence: str, fragment: str, start: int = 0) -> Optional[Span]:
    """Locate `fragment` inside `sentence`, searching from `start` first, then anywhere."""
    frag = _LEADING_PUNCT.sub("", _TRAILING_PUNCT.sub("", fragment.strip()))
    if not frag:
        return None
    idx = sentence.find(frag, start)
    if idx < 0:
        idx = sentence.find(frag)
    if idx < 0:
        return None
    return (idx, idx + len(frag))


def chunk_spans(lesson: Lesson) -> List[Optional[Span]]:
    """Spans of each chunk in the sentence, matched left to right so repeats land in order."""
    spans: List[Optional[Span]] = []
    cursor = 0
    for chunk in lesson.chunks:
        span = find_span(lesson.japanese, chunk.japanese, cursor)
        spans.append(span)
        if span:
            cursor = span[1]
    return spans


def intro_utterance(japanese: str) -> Utterance:
    """The first thing the learner hears: the line itself, slowly. Available before the lesson is."""
    return Utterance("ja", japanese, slow=True, pause_after=0.6, span=(0, len(japanese)))


def build_script(lesson: Lesson, *, quiz_pause: float = 2.0, full_breakdown: bool = True, skip_intro: bool = False) -> List[Utterance]:
    """Return the spoken sequence for one in-game line. `skip_intro` when the
    Japanese line was already played while the lesson was being generated."""
    s: List[Utterance] = []
    whole: Span = (0, len(lesson.japanese))

    # 1. Hear it: the line in Japanese, slowly.
    if not skip_intro:
        s.append(intro_utterance(lesson.japanese))
    # 2. Understand it: English translation, plus the register when it matters.
    s.append(Utterance("en", lesson.english, pause_after=0.6, span=whole))
    if full_breakdown and lesson.tone.strip():
        s.append(Utterance("en", lesson.tone.strip(), pause_after=0.5, span=whole))

    if not full_breakdown:
        s.append(Utterance("ja", lesson.japanese, pause_after=0.5, span=whole))
        return s

    # 3. Take it apart: the highlight follows each piece while it is read and explained.
    s.append(Utterance("en", "Let's break that down.", pause_after=0.3))
    for chunk, span in zip(lesson.chunks, chunk_spans(lesson)):
        s.append(Utterance("ja", chunk.reading or chunk.japanese, slow=True, pause_after=0.2, span=span, reading=chunk.reading))
        line = chunk.meaning.strip().rstrip(".")
        if chunk.note.strip():
            line = f"{line}. {chunk.note.strip()}"
        s.append(Utterance("en", line, pause_after=0.4, span=span, reading=chunk.reading))

    # 4. Word order, in one breath.
    if lesson.literal.strip():
        s.append(Utterance("en", f"So, piece by piece, it reads: {lesson.literal.strip()}", pause_after=0.5, span=whole))

    # 5. Rebuild it: question (no highlight, the learner is thinking), pause, answer
    # (highlight the piece). The answer is spoken from the kanji text: the Japanese
    # voice parses mixed text better than bare kana.
    for step in lesson.build_up:
        s.append(Utterance("en", step.prompt_en, pause_after=quiz_pause))
        s.append(Utterance("ja", step.japanese or step.reading, pause_after=0.6, span=find_span(lesson.japanese, step.japanese), reading=step.reading))

    # 6. The takeaway.
    if lesson.pattern.strip():
        s.append(Utterance("en", lesson.pattern.strip(), pause_after=0.5))

    # 7. Once more, at speed, with meaning.
    s.append(Utterance("en", "Once more.", pause_after=0.2))
    s.append(Utterance("ja", lesson.japanese, pause_after=0.4, span=whole))
    s.append(Utterance("en", lesson.english, pause_after=0.8, span=whole))
    return s


def mark_span(sentence: str, span: Optional[Span], left: str = "【", right: str = "】") -> str:
    """Render the sentence with the span bracketed, for text displays."""
    if not span:
        return sentence
    a, b = span
    return f"{sentence[:a]}{left}{sentence[a:b]}{right}{sentence[b:]}"


def script_as_text(script: List[Utterance]) -> str:
    return "\n".join(f"[{u.lang}{' slow' if u.slow else ''}] {u.text}" for u in script)

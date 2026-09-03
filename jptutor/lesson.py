"""Data models exchanged with Claude (structured outputs) and used by the speech script."""

from __future__ import annotations

import re
from typing import List, Literal

from pydantic import BaseModel, Field

JAPANESE_RE = re.compile(r"[぀-ゟ゠-ヿ一-鿿ｦ-ﾟ]")
_SENTENCE_END = re.compile(r"(?<=[。！？!?])(?![」』）)])\s*")


def contains_japanese(text: str) -> bool:
    return bool(JAPANESE_RE.search(text))


def split_sentences(text: str) -> List[str]:
    """Split a dialogue box into sentences on Japanese sentence enders, keeping the ender."""
    parts = [p.strip() for p in _SENTENCE_END.split(text.strip()) if p and p.strip()]
    return parts or [text.strip()]


class OcrLine(BaseModel):
    text: str = Field(description="The Japanese text exactly as written on screen, with no furigana.")
    kind: Literal["dialogue", "narration", "choice", "menu", "system", "other"] = Field(
        description="dialogue = a character speaking; narration = story text; choice = an option the player picks; menu/system = UI labels, button prompts, HUD."
    )
    speaker: str = Field(default="", description="Speaker name if shown next to the line, otherwise empty.")
    complete: bool = Field(default=True, description="False if the line is still being typed out or is cut off mid-phrase.")


class OcrResult(BaseModel):
    lines: List[OcrLine] = Field(description="Every distinct piece of Japanese text visible, in reading order.")


class Chunk(BaseModel):
    japanese: str = Field(description="One meaningful piece of the sentence: a word, particle, or short phrase.")
    reading: str = Field(description="Reading of the chunk in hiragana (katakana allowed for loanwords).")
    meaning: str = Field(description="Plain English meaning of this chunk, a few words.")
    note: str = Field(
        default="",
        description="One short, friendly teaching sentence: a memory hook, what the particle does, a cognate, or how the form is built. Empty if nothing worth saying.",
    )


class BuildStep(BaseModel):
    prompt_en: str = Field(description="A question to the learner, e.g. 'So how would you say: I will go to school?'")
    japanese: str = Field(description="The Japanese answer for this step.")
    reading: str = Field(description="Hiragana reading of the answer.")


class Lesson(BaseModel):
    japanese: str = Field(description="The full sentence as displayed in the game.")
    reading: str = Field(description="Full sentence in hiragana/katakana only, for pronunciation.")
    english: str = Field(description="Natural English translation.")
    literal: str = Field(description="Word-order-preserving gloss, e.g. 'school-to go-(polite)'.")
    chunks: List[Chunk] = Field(description="The sentence split into pieces, in the original order.")
    build_up: List[BuildStep] = Field(
        description="Two to four steps that rebuild the sentence from its smallest piece to the full line, each posed as a question to the learner."
    )
    pattern: str = Field(
        description="The one reusable takeaway: the grammar pattern or structure this sentence demonstrates, explained in one or two sentences."
    )
    tone: str = Field(
        default="",
        description="One spoken sentence on the register of the line (rough, polite, archaic, cute, who talks like this) when notable; empty when plain.",
    )

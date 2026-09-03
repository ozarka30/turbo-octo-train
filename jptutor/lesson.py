"""Data models exchanged with Claude (structured outputs) and used by the speech script."""

from __future__ import annotations

import re
from typing import List, Literal

from pydantic import BaseModel, Field

JAPANESE_RE = re.compile(r"[぀-ゟ゠-ヿ一-鿿ｦ-ﾟ]")


def contains_japanese(text: str) -> bool:
    return bool(JAPANESE_RE.search(text))


class OcrLine(BaseModel):
    text: str = Field(description="The Japanese text exactly as written on screen, with no furigana.")
    kind: Literal["dialogue", "narration", "menu", "system", "other"] = Field(
        description="dialogue = a character speaking; narration = story text; menu/system = UI labels, button prompts, HUD."
    )
    speaker: str = Field(default="", description="Speaker name if shown next to the line, otherwise empty.")


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

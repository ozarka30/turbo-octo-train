"""A canned tutor backend for tests and offline demos (no API calls)."""

from __future__ import annotations

from typing import List

from PIL import Image

from .lesson import BuildStep, Chunk, Lesson, OcrLine, OcrResult

SAMPLE_LESSON = Lesson(
    japanese="学校に行きます。",
    reading="がっこうにいきます。",
    english="I'm going to school.",
    literal="school-to go-polite",
    chunks=[
        Chunk(japanese="学校", reading="がっこう", meaning="school", note="Gakkou. The kou at the end is the same kou as in koukou, high school."),
        Chunk(japanese="に", reading="に", meaning="to, toward", note="The little word ni points at where you are heading."),
        Chunk(japanese="行きます", reading="いきます", meaning="go, polite form", note="Iku means go. Swap the ku for ki, add masu, and you have the polite form: ikimasu."),
    ],
    build_up=[
        BuildStep(prompt_en="So how would you say: to school?", japanese="学校に", reading="がっこうに"),
        BuildStep(prompt_en="And: I go to school?", japanese="学校に行きます。", reading="がっこうにいきます。"),
    ],
    pattern="In Japanese the verb goes at the end. Say the where first, then the going.",
)


class FakeTutor:
    def __init__(self, ocr_lines: List[OcrLine] | None = None):
        self.ocr_lines = ocr_lines if ocr_lines is not None else [
            OcrLine(text="学校に行きます。", kind="dialogue", speaker="ユウ"),
            OcrLine(text="HP 120/120", kind="system"),
            OcrLine(text="セーブ", kind="menu"),
        ]
        self.teach_calls: List[str] = []
        self.ocr_calls = 0

    def ocr(self, image: Image.Image) -> OcrResult:
        self.ocr_calls += 1
        return OcrResult(lines=self.ocr_lines)

    def teach(self, japanese: str, *, speaker: str = "", context: str = "", history=()) -> Lesson:
        self.teach_calls.append(japanese)
        return SAMPLE_LESSON.model_copy(update={"japanese": japanese})

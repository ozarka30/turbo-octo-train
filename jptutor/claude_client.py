"""Thin wrapper around the Claude API: vision OCR and lesson generation."""

from __future__ import annotations

import base64
import io
import logging
from typing import List, Optional, Protocol

import anthropic
from PIL import Image

from .config import Settings
from .lesson import Lesson, OcrResult
from .prompts import OCR_SYSTEM, TUTOR_SYSTEM, TUTOR_USER_TEMPLATE

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class RefusedError(RuntimeError):
    """Claude declined the request (stop_reason == 'refusal')."""


class TutorBackend(Protocol):
    def ocr(self, image: Image.Image) -> OcrResult: ...

    def teach(self, japanese: str, *, speaker: str = "", context: str = "", history: List[str] = ()) -> Lesson: ...


def _png_b64(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG", optimize=True)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def _check_refusal(response) -> None:
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        why = getattr(details, "explanation", None) or "no explanation given"
        raise RefusedError(f"Claude declined this request: {why}")
    if response.stop_reason == "max_tokens":
        log.warning("response was cut off at max_tokens; the lesson may be incomplete")


class ClaudeTutor:
    """OCR and lessons via the Anthropic SDK.

    Both calls use structured outputs (`parse` + a pydantic model), a cached system
    prompt, adaptive thinking (the Opus 5 default), and server-side refusal
    fallbacks so a stray classifier decline does not stall the game session.
    """

    def __init__(self, settings: Settings, client: Optional[anthropic.Anthropic] = None):
        self.settings = settings
        self.client = client or anthropic.Anthropic()

    def ocr(self, image: Image.Image) -> OcrResult:
        response = self.client.beta.messages.parse(
            model=self.settings.ocr_model,
            max_tokens=4096,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": self.settings.ocr_effort},
            system=[{"type": "text", "text": OCR_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": _png_b64(image)},
                        },
                        {"type": "text", "text": "List the Japanese text in this screenshot."},
                    ],
                }
            ],
            output_format=OcrResult,
        )
        _check_refusal(response)
        return response.parsed_output

    def teach(
        self,
        japanese: str,
        *,
        speaker: str = "",
        context: str = "",
        history: List[str] = (),
    ) -> Lesson:
        user = TUTOR_USER_TEMPLATE.format(
            history="\n".join(history) if history else "(nothing yet)",
            context=context or "unknown game",
            speaker=speaker or "unknown",
            japanese=japanese,
        )
        response = self.client.beta.messages.parse(
            model=self.settings.tutor_model,
            max_tokens=16000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": self.settings.tutor_effort},
            system=[
                {
                    "type": "text",
                    "text": TUTOR_SYSTEM.format(level=self.settings.level),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
            output_format=Lesson,
        )
        _check_refusal(response)
        usage = response.usage
        log.debug(
            "lesson tokens: in=%s cached=%s out=%s",
            usage.input_tokens,
            getattr(usage, "cache_read_input_tokens", 0),
            usage.output_tokens,
        )
        return response.parsed_output

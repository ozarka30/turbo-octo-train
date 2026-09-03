"""Thin wrapper around the Claude API: vision OCR and lesson generation."""

from __future__ import annotations

import base64
import io
import logging
from typing import List, Optional, Protocol

import anthropic
from PIL import Image

from .config import Settings
from .errors import FatalError
from .lesson import Lesson, OcrResult
from .cache import OcrCache, png_bytes
from .prompts import OCR_SYSTEM, OCR_USER, build_knowledge_block, build_tutor_system, build_tutor_user
from .usage import get_meter

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class RefusedError(RuntimeError):
    """Claude declined the request (stop_reason == 'refusal')."""


class TutorBackend(Protocol):
    def ocr(self, image: Image.Image) -> OcrResult: ...

    def teach(self, japanese: str, *, speaker: str = "", context: str = "", full_line: str = "", knowledge: str = "", recent: str = "") -> Lesson: ...


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
        self.ocr_cache = OcrCache(settings.cache_dir, settings.ocr_model)
        self.meter = get_meter()

    def _cache(self) -> dict:
        return {"type": "ephemeral", "ttl": self.settings.cache_ttl}

    def _parse(self, **kwargs):
        try:
            return self.client.beta.messages.parse(**kwargs)
        except anthropic.AuthenticationError as e:
            raise FatalError(f"Anthropic API rejected the credentials: {e.message}. Check ANTHROPIC_API_KEY.") from e
        except anthropic.PermissionDeniedError as e:
            raise FatalError(f"Anthropic API refused access: {e.message}") from e
        except anthropic.NotFoundError as e:
            raise FatalError(f"Model not found: {e.message}. Check JPTUTOR_TUTOR_MODEL / JPTUTOR_OCR_MODEL.") from e

    def ocr(self, image: Image.Image) -> OcrResult:
        png = png_bytes(image)
        cached = self.ocr_cache.get(png)
        if cached is not None:
            return cached
        response = self._parse(
            model=self.settings.ocr_model,
            max_tokens=4096,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": self.settings.ocr_effort},
            system=[{"type": "text", "text": OCR_SYSTEM, "cache_control": self._cache()}],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(png).decode("ascii")},
                        },
                        {"type": "text", "text": OCR_USER},
                    ],
                }
            ],
            output_format=OcrResult,
        )
        _check_refusal(response)
        self.meter.record_api("api", self.settings.ocr_model, "ocr", response.usage, self.settings.cache_ttl)
        result = response.parsed_output
        self.ocr_cache.put(png, result)
        return result

    def teach(
        self,
        japanese: str,
        *,
        speaker: str = "",
        context: str = "",
        full_line: str = "",
        knowledge: str = "",
        recent: str = "",
    ) -> Lesson:
        user = build_tutor_user(japanese, speaker=speaker, context=context, full_line=full_line, recent=recent)
        # Two cached blocks: the frozen tutor prompt, then the memory snapshot, which only
        # changes every few lessons. Per-lesson detail goes in the user message, after
        # the breakpoints, so it never invalidates them.
        system = [
            {"type": "text", "text": build_tutor_system(self.settings.level), "cache_control": self._cache()},
            {"type": "text", "text": build_knowledge_block(knowledge), "cache_control": self._cache()},
        ]
        response = self._parse(
            model=self.settings.tutor_model,
            max_tokens=16000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": self.settings.tutor_effort},
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=Lesson,
        )
        _check_refusal(response)
        call = self.meter.record_api("api", self.settings.tutor_model, "lesson", response.usage, self.settings.cache_ttl)
        log.debug("lesson tokens: in=%s cached=%s written=%s out=%s", call.input_tokens, call.cache_read, call.cache_write, call.output_tokens)
        return response.parsed_output

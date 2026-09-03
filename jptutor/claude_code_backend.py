"""Tutor backend that runs Claude Code's headless mode (`claude -p`).

Use this when you want the tutor to run on your claude.ai subscription login
instead of an API key. Claude Code itself is the only officially documented
way to do that: `claude -p` (without `--bare`) uses the login you set up with
`claude` / `/login`, supports `--json-schema` for validated structured output,
and its Read tool can look at image files.

Each call spawns one `claude -p` process, so expect a few seconds of startup
per line on top of the model's own time.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Type, TypeVar

from PIL import Image
from pydantic import BaseModel

from .config import Settings
from .errors import FatalError
from .lesson import Lesson, OcrResult
from .cache import OcrCache, png_bytes
from .prompts import OCR_SYSTEM, build_knowledge_block, build_tutor_system, build_tutor_user
from .usage import get_meter

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

INSTALL_HINT = (
    "Claude Code CLI not found on PATH. Install it (https://code.claude.com/docs/en/overview), "
    "run `claude` once and log in with your subscription, then retry."
)


NO_TOOLS = ["Bash", "Read", "Edit", "Write", "NotebookEdit", "Glob", "Grep", "WebFetch", "WebSearch", "Agent"]


class ClaudeCodeError(RuntimeError):
    pass


class ClaudeCodeTutor:
    def __init__(self, settings: Settings, *, binary: str = "claude", timeout: float = 300.0, runner=None):
        self.settings = settings
        self.binary = shutil.which(binary) or binary  # full path: on Windows the npm shim is claude.cmd
        self.timeout = timeout
        self._run = runner or subprocess.run
        # An empty working directory: no project CLAUDE.md / .mcp.json gets loaded,
        # and screenshots saved here are readable without a permission prompt.
        self.workdir = settings.cache_dir / "claude-work"
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.ocr_cache = OcrCache(settings.cache_dir, settings.ocr_model)
        self.meter = get_meter()

    # ---------------------------------------------------------------- helpers
    def _base_args(self, model: str, effort: str, schema: dict, max_turns: int) -> List[str]:
        return [
            self.binary,
            "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            "--model", model,
            "--effort", effort,
            "--permission-mode", "dontAsk",
            "--no-session-persistence",
            "--max-turns", str(max_turns),
        ]

    def _invoke(self, args: List[str], prompt: str, model_cls: Type[T], *, model: str = "", kind: str = "") -> T:
        if shutil.which(self.binary) is None and not Path(self.binary).exists():
            raise FatalError(INSTALL_HINT)
        started = time.monotonic()
        try:
            # The prompt goes in on stdin: --allowedTools / --disallowedTools take
            # several values, so a trailing positional prompt would be eaten by them.
            proc = self._run(
                args,
                input=prompt,
                cwd=str(self.workdir),
                capture_output=True,
                text=True,
                encoding="utf-8",  # Japanese on stdin/stdout regardless of the console code page
                errors="replace",
                timeout=self.timeout,
            )
        except FileNotFoundError as e:
            raise FatalError(INSTALL_HINT) from e
        except subprocess.TimeoutExpired as e:
            raise ClaudeCodeError(f"claude -p timed out after {self.timeout:.0f}s") from e
        log.debug("claude -p finished in %.1fs (exit %s)", time.monotonic() - started, proc.returncode)
        if proc.returncode != 0:
            raise ClaudeCodeError(f"claude -p failed (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[:500]}")
        result, envelope = self._parse(proc.stdout, model_cls)
        if envelope:
            self.meter.record_claude_code(model, kind, envelope)
        return result

    @staticmethod
    def _parse(stdout: str, model_cls: Type[T]):
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as e:
            raise ClaudeCodeError(f"claude -p did not return JSON: {stdout[:300]!r}") from e
        if isinstance(envelope, list):  # stream-json style: last message is the result
            envelope = envelope[-1]
        if envelope.get("is_error"):
            msg = str(envelope.get("result", ""))[:500]
            if re.search(r"log ?in|authenticat|api key|credential|not authorized", msg, re.I):
                raise FatalError(f"Claude Code is not logged in: {msg}. Run `claude` once and log in.")
            raise ClaudeCodeError(f"claude -p reported an error: {msg}")
        payload = envelope.get("structured_output")
        if payload is None:
            result = envelope.get("result", "")
            try:
                payload = json.loads(result)
            except (json.JSONDecodeError, TypeError) as e:
                raise ClaudeCodeError(f"no structured_output in claude -p response: {result[:300]!r}") from e
        return model_cls.model_validate(payload), envelope

    # ---------------------------------------------------------------- backend
    def ocr(self, image: Image.Image) -> OcrResult:
        png = png_bytes(image)
        cached = self.ocr_cache.get(png)
        if cached is not None:
            return cached
        fd, name = tempfile.mkstemp(prefix="shot-", suffix=".png", dir=self.workdir)
        os.close(fd)
        path = Path(name)
        try:
            path.write_bytes(png)
            args = self._base_args(self.settings.ocr_model, self.settings.ocr_effort, OcrResult.model_json_schema(), max_turns=6)
            args += ["--append-system-prompt", OCR_SYSTEM, "--allowedTools", "Read", "StructuredOutput"]
            prompt = (
                f"Use the Read tool to look at the screenshot file ./{path.name} in this directory, "
                "then list every piece of Japanese text in it."
            )
            result = self._invoke(args, prompt, OcrResult, model=self.settings.ocr_model, kind="ocr")
            self.ocr_cache.put(png, result)
            return result
        finally:
            path.unlink(missing_ok=True)

    def teach(self, japanese: str, *, speaker: str = "", context: str = "", full_line: str = "", knowledge: str = "", recent: str = "") -> Lesson:
        prompt = build_tutor_user(japanese, speaker=speaker, context=context, full_line=full_line, recent=recent)
        args = self._base_args(self.settings.tutor_model, self.settings.tutor_effort, Lesson.model_json_schema(), max_turns=4)
        # --json-schema output arrives through the StructuredOutput tool, so it must stay allowed;
        # everything else is removed so the lesson never touches files or the shell.
        # Memory snapshot rides in the system prompt so Claude Code's own prompt cache covers it.
        system = build_tutor_system(self.settings.level) + "\n" + build_knowledge_block(knowledge)
        args += ["--system-prompt", system]
        args += ["--allowedTools", "StructuredOutput", "--disallowedTools", *NO_TOOLS]
        return self._invoke(args, prompt, Lesson, model=self.settings.tutor_model, kind="lesson")


def claude_code_available(binary: str = "claude") -> Optional[str]:
    return shutil.which(binary)

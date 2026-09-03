"""Pick which Claude backend to use."""

from __future__ import annotations

import os
import shutil

from .config import Settings

NO_BACKEND_MSG = """No way to reach Claude was found. Pick one:

  * Claude subscription: install Claude Code (https://code.claude.com/docs/en/overview),
    run `claude` once and log in, then run jptutor again (it shells out to `claude -p`).
  * API key: set ANTHROPIC_API_KEY (https://platform.claude.com/settings/keys).

Force a choice with JPTUTOR_BACKEND=claude-code or JPTUTOR_BACKEND=api, or --backend."""


def resolve_backend(settings: Settings, env=None) -> str:
    env = os.environ if env is None else env
    choice = settings.backend
    if choice == "auto":
        if env.get("ANTHROPIC_API_KEY"):
            choice = "api"
        elif shutil.which("claude"):
            choice = "claude-code"
        else:
            raise SystemExit(NO_BACKEND_MSG)
    if choice not in ("api", "claude-code"):
        raise SystemExit(f"unknown backend {choice!r}; use api or claude-code")
    return choice


def make_tutor(settings: Settings, *, offline: bool = False):
    if offline:
        from .fake import FakeTutor

        return FakeTutor()
    backend = resolve_backend(settings)
    if backend == "claude-code":
        from .claude_code_backend import ClaudeCodeTutor

        return ClaudeCodeTutor(settings)
    from .claude_client import ClaudeTutor

    return ClaudeTutor(settings)

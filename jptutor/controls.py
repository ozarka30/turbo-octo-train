"""Playback controls: skip, pause, repeat, capture, practice, quit.

The flags live here; hotkeys (optional, via pynput) and the terminal both
set them. The speaker checks `skip`/`paused` between utterances and the
audio player watches `skip` mid-clip.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, Optional

log = logging.getLogger(__name__)

DEFAULT_HOTKEYS = {
    "capture": "<f8>",
    "skip": "<f9>",
    "pause": "<f10>",
    "repeat": "<f11>",
    "practice": "<f7>",
}


class Controls:
    def __init__(self):
        self.skip = threading.Event()  # abandon the current lesson
        self.paused = threading.Event()  # set = paused
        self.actions: Dict[str, Callable[[], None]] = {}  # capture / repeat / practice / quit handlers
        self.hotkey_listener = None

    # --- things the UI calls
    def request_skip(self) -> None:
        self.skip.set()
        self.paused.clear()

    def toggle_pause(self) -> bool:
        if self.paused.is_set():
            self.paused.clear()
        else:
            self.paused.set()
        return self.paused.is_set()

    def fire(self, action: str) -> None:
        fn = self.actions.get(action)
        if fn is None:
            log.info("no handler for %s", action)
            return
        try:
            fn()
        except Exception:
            log.exception("control %s failed", action)

    # --- things the speaker calls
    def wait_if_paused(self, stop: threading.Event, poll: float = 0.1) -> None:
        while self.paused.is_set() and not stop.is_set() and not self.skip.is_set():
            stop.wait(poll)

    def begin_lesson(self) -> None:
        self.skip.clear()


def parse_hotkeys(spec: str) -> Dict[str, str]:
    """'capture=<f8>,skip=<f9>' -> mapping; unknown actions ignored."""
    mapping = dict(DEFAULT_HOTKEYS)
    for part in spec.split(","):
        if "=" in part:
            action, key = part.split("=", 1)
            if action.strip() in mapping:
                mapping[action.strip()] = key.strip()
    return mapping


def start_hotkeys(controls: Controls, mapping: Optional[Dict[str, str]] = None):
    """Register global hotkeys. Returns the listener, or None when pynput is unavailable."""
    mapping = mapping or DEFAULT_HOTKEYS
    try:
        from pynput import keyboard
    except Exception as e:  # ImportError, or no display server
        log.info("hotkeys unavailable: %s", e)
        return None

    handlers = {
        mapping["skip"]: controls.request_skip,
        mapping["pause"]: lambda: log.info("paused" if controls.toggle_pause() else "resumed"),
        mapping["capture"]: lambda: controls.fire("capture"),
        mapping["repeat"]: lambda: controls.fire("repeat"),
        mapping["practice"]: lambda: controls.fire("practice"),
    }
    try:
        listener = keyboard.GlobalHotKeys(handlers)
        listener.daemon = True
        listener.start()
    except Exception as e:
        log.warning("could not start hotkeys: %s", e)
        return None
    controls.hotkey_listener = listener
    return listener

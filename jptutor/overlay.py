"""An always-on-top overlay window that shows the sentence and highlights the
piece being read or explained. Needs tkinter (bundled with most Pythons).

Tk must run on the main thread, so `Overlay.run(worker)` runs the tutor on a
background thread and the window on the main thread; the pipeline talks to the
window through a queue.
"""

from __future__ import annotations

import platform
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from typing import Callable, Optional, Tuple

from .lesson import Lesson
from .script import Utterance

Geometry = Tuple[int, int, int, int]  # x, y, w, h

BG = "#101418"
FG = "#f2f2f2"
DIM = "#9aa4ad"
HL_BG = "#ffd23f"
HL_FG = "#101418"


def make_dpi_aware() -> None:
    """On Windows, opt in to per-monitor DPI so Tk coordinates match the pixels mss grabs."""
    if platform.system() != "Windows":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def parse_geometry(value: str) -> Geometry:
    x, y, w, h = (int(p.strip()) for p in value.split(","))
    return (x, y, w, h)


class Overlay:
    def __init__(self, geometry: Optional[Geometry] = None, font_size: int = 34, opacity: float = 0.88):
        make_dpi_aware()
        self.root = tk.Tk()
        self.root.withdraw()
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._done = threading.Event()
        self.stop_event = threading.Event()  # set when the window closes; the tutor thread watches it
        self._worker_error: Optional[BaseException] = None

        r = self.root
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        try:
            r.attributes("-alpha", opacity)
        except tk.TclError:
            pass
        r.configure(bg=BG)

        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        if geometry is None:
            w = int(sw * 0.7)
            h = int(font_size * 5.2)
            geometry = ((sw - w) // 2, sh - h - 60, w, h)
        x, y, w, h = geometry
        r.geometry(f"{w}x{h}+{x}+{y}")

        self.ja_font = tkfont.Font(family="TkDefaultFont", size=font_size)
        self.small_font = tkfont.Font(family="TkDefaultFont", size=max(12, font_size // 2))
        self.cap_font = tkfont.Font(family="TkDefaultFont", size=max(13, int(font_size * 0.55)))

        pad = 14
        self.text = tk.Text(
            r, height=1, font=self.ja_font, bg=BG, fg=FG, bd=0, highlightthickness=0,
            wrap="char", cursor="arrow", insertwidth=0, selectbackground=BG,
        )
        self.text.tag_configure("hl", background=HL_BG, foreground=HL_FG)
        self.text.tag_configure("center", justify="center")
        self.text.pack(fill="x", padx=pad, pady=(pad, 4))
        self.reading = tk.Label(r, font=self.small_font, bg=BG, fg=HL_BG)
        self.reading.pack(fill="x", padx=pad)
        self.caption = tk.Label(r, font=self.cap_font, bg=BG, fg=FG, wraplength=w - 2 * pad, justify="center")
        self.caption.pack(fill="x", padx=pad, pady=(4, pad))
        self.text.bind("<Key>", lambda e: "break")

        # Drag anywhere to move; Escape (once the window has focus) or the × closes.
        for widget in (r, self.text, self.reading, self.caption):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
        r.bind_all("<Escape>", lambda e: self.close())
        self.close_btn = tk.Label(r, text="×", font=self.small_font, bg=BG, fg=DIM, cursor="hand2")
        self.close_btn.place(relx=1.0, x=-6, y=2, anchor="ne")
        self.close_btn.bind("<ButtonPress-1>", lambda e: self.close())
        self._drag = (0, 0)
        self._set_sentence("jptutor is listening…", dim=True)

    # ------------------------------------------------------------- thread-safe API
    def show_sentence(self, japanese: str) -> None:
        self._q.put(("sentence", japanese))

    def show_lesson(self, lesson: Lesson) -> None:
        self._q.put(("lesson", lesson))

    def on_utterance(self, u: Utterance) -> None:
        self._q.put(("utt", u))

    def finish(self) -> None:
        self._q.put(("finish",))

    def show_error(self, message: str) -> None:
        self._q.put(("error", message))

    def show_practice(self, heard_kana: str, score, *, listening: bool) -> None:
        self._q.put(("practice", heard_kana, score, listening))

    def close(self) -> None:
        self.stop_event.set()
        self._q.put(("close",))

    # ------------------------------------------------------------- main thread
    def run(self, worker: Callable[["Overlay"], None]) -> None:
        """Run `worker(self)` on a thread while the window runs on this (main) thread."""

        def body():
            try:
                worker(self)
            except BaseException as e:  # surfaced after mainloop ends
                self._worker_error = e
            finally:
                self._done.set()
                self._q.put(("close",))

        thread = threading.Thread(target=body, name="jptutor-tutor", daemon=True)
        thread.start()
        self.root.deiconify()
        self.root.after(40, self._poll)
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        # Window gone: tell the tutor thread to wind down and give it a moment to do so
        # (it finishes the current clip, writes memory, prints the usage line).
        self.stop_event.set()
        thread.join(timeout=15)
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        if self._worker_error and not isinstance(self._worker_error, KeyboardInterrupt):
            raise self._worker_error

    def _poll(self) -> None:
        try:
            while True:
                event = self._q.get_nowait()
                if event[0] == "close":
                    self.root.quit()
                    return
                self._apply(event)
        except queue.Empty:
            pass
        self.root.after(40, self._poll)

    def _apply(self, event: tuple) -> None:
        kind = event[0]
        if kind in ("lesson", "sentence"):
            sentence = event[1].japanese if kind == "lesson" else event[1]
            if getattr(self, "_sentence", None) != sentence:
                self._sentence = sentence
                self._set_sentence(sentence)
                self.reading.configure(text="")
                self.caption.configure(text="")
        elif kind == "utt":
            u: Utterance = event[1]
            self._highlight(u.span)
            self.reading.configure(text=u.reading if (u.reading and u.span) else "")
            # English is captioned; a Japanese utterance clears the caption so a stale
            # explanation never sits under a different highlight.
            self.caption.configure(text=u.text if u.lang == "en" else "")
        elif kind == "finish":
            self._highlight(None)
            self.reading.configure(text="")
            self.caption.configure(text="")
        elif kind == "practice":
            _, heard, score, listening = event
            if listening:
                self._highlight((0, len(getattr(self, "_sentence", ""))))
                self.reading.configure(text="🎤 your turn", fg="#7ee787")
                self.caption.configure(text="")
            else:
                pct = int((score or 0) * 100)
                colour = "#7ee787" if pct >= 90 else ("#ffd23f" if pct >= 70 else "#ff7b72")
                self.reading.configure(text=f"heard: {heard or '…'}   {pct}%", fg=colour)
                self.root.after(6000, lambda: self.reading.configure(fg=HL_BG))
        elif kind == "error":
            self.reading.configure(text="")
            self.caption.configure(text=event[1], fg="#ff7b72")
            self.root.after(8000, lambda: self.caption.configure(fg=FG))

    def _set_sentence(self, sentence: str, dim: bool = False) -> None:
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        # A leading space keeps a highlight that starts at character 0 from painting the
        # centred line's left padding; spans are offset by one in _highlight.
        t.insert("1.0", " " + sentence + " ", ("center",))
        t.configure(fg=DIM if dim else FG, height=max(1, min(3, 1 + len(sentence) // 22)))
        t.configure(state="disabled")

    def _highlight(self, span) -> None:
        t = self.text
        t.tag_remove("hl", "1.0", "end")
        if span:
            a, b = span
            t.tag_add("hl", f"1.0+{a + 1}c", f"1.0+{b + 1}c")

    def _drag_start(self, e) -> None:
        self.root.focus_force()  # so Escape reaches us on platforms that never focus borderless windows
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag_move(self, e) -> None:
        dx, dy = self._drag
        self.root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

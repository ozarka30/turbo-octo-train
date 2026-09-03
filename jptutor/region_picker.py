"""A full-screen transparent overlay to drag-select the dialogue box (needs tkinter)."""

from __future__ import annotations

import tkinter as tk
from typing import Optional

from .config import Region


def pick_region() -> Optional[Region]:
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.3)
    root.configure(bg="black")
    canvas = tk.Canvas(root, cursor="cross", bg="black", highlightthickness=0)
    canvas.pack(fill=tk.BOTH, expand=True)
    canvas.create_text(20, 20, anchor="nw", fill="white", font=("Helvetica", 16),
                       text="Drag a box around the game's dialogue area. Esc to cancel.")
    state = {"start": None, "rect": None, "result": None}

    def on_press(e):
        state["start"] = (e.x_root, e.y_root)
        state["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="yellow", width=2)

    def on_drag(e):
        if state["rect"] is not None:
            x0, y0 = state["start"]
            canvas.coords(state["rect"], x0 - root.winfo_rootx(), y0 - root.winfo_rooty(), e.x, e.y)

    def on_release(e):
        x0, y0 = state["start"]
        x1, y1 = e.x_root, e.y_root
        x, y = min(x0, x1), min(y0, y1)
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w > 4 and h > 4:
            state["result"] = (x, y, w, h)
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", lambda e: root.destroy())
    root.mainloop()
    return state["result"]

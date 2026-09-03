"""Command line entry point: `jptutor ...`"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .config import Settings, parse_region

log = logging.getLogger("jptutor")


def _settings(args) -> Settings:
    s = Settings.from_env()
    if getattr(args, "region", None):
        s.region = parse_region(args.region)
    if getattr(args, "level", None):
        s.level = args.level
    if getattr(args, "model", None):
        s.tutor_model = args.model
    if getattr(args, "backend", None):
        s.backend = args.backend
    return s


def _tutor(settings: Settings, offline: bool = False):
    from .backends import make_tutor

    return make_tutor(settings, offline=offline)


def cmd_teach(args) -> int:
    """Teach one sentence typed on the command line (no screen capture)."""
    from .pipeline import TutorPipeline
    from .tts import make_speaker

    settings = _settings(args)
    speaker = make_speaker(settings, "console" if args.dry_run else "edge")
    pipe = TutorPipeline(_tutor(settings, args.offline), speaker, settings, context=args.context, full_breakdown=not args.quick)
    for sentence in args.sentence:
        print(f"\n== {sentence}")
        for lesson in pipe.teach_line(sentence):
            if args.show:
                print(lesson.model_dump_json(indent=2, ensure_ascii=False))
    return 0


def cmd_image(args) -> int:
    """Run the full pipeline on an existing screenshot file."""
    from PIL import Image

    from .pipeline import TutorPipeline
    from .tts import make_speaker

    settings = _settings(args)
    speaker = make_speaker(settings, "console" if args.dry_run else "edge")
    pipe = TutorPipeline(_tutor(settings, args.offline), speaker, settings, context=args.context, full_breakdown=not args.quick)
    for path in args.image:
        frame = Image.open(path)
        if settings.region:
            x, y, w, h = settings.region
            frame = frame.crop((x, y, x + w, y + h))
        print(f"\n== {path}")
        taught = pipe.handle_frame(frame)
        if not taught:
            print("  (no new dialogue found)")
    return 0


def cmd_watch(args) -> int:
    """Watch the screen and teach each new line as it appears."""
    from .capture import ChangeDetector, ScreenGrabber
    from .pipeline import FrameWorker, TutorPipeline
    from .tts import make_speaker

    settings = _settings(args)
    if settings.region is None:
        print("warning: no --region given, capturing the whole primary monitor. Set JPTUTOR_REGION or --region x,y,w,h to the dialogue box for better results.", file=sys.stderr)
    speaker = make_speaker(settings, "console" if args.dry_run else "edge")
    pipe = TutorPipeline(_tutor(settings, args.offline), speaker, settings, context=args.context, full_breakdown=not args.quick)
    worker = FrameWorker(pipe, settings.max_queue).start()
    grabber = ScreenGrabber(settings.region)

    if args.manual:
        print("Manual mode: press Enter to read the screen, Ctrl-C to quit.")
        try:
            while True:
                input()
                worker.submit(grabber.grab())
        except (KeyboardInterrupt, EOFError):
            pass
    else:
        detector = ChangeDetector(settings.change_threshold, settings.stability_frames)
        print(f"Watching region {settings.region or 'full screen'} every {settings.poll_interval}s. Ctrl-C to quit.")
        try:
            for frame in grabber.watch(detector, settings.poll_interval):
                worker.submit(frame)
        except KeyboardInterrupt:
            pass
    worker.stop()
    print(f"\nbye. lines taught: {len(pipe.lessons)}, frames dropped: {worker.dropped}")
    return 0


def cmd_snapshot(args) -> int:
    """Save a screenshot so you can work out the dialogue-box coordinates."""
    from .capture import ScreenGrabber

    settings = _settings(args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    ScreenGrabber(settings.region).grab().save(out)
    print(f"saved {out}")
    return 0


def cmd_select_region(args) -> int:
    """Drag a rectangle over the screen to pick the dialogue box."""
    try:
        from .region_picker import pick_region
    except ImportError as e:  # tkinter missing
        print(f"region picker unavailable: {e}", file=sys.stderr)
        return 1
    region = pick_region()
    if region is None:
        print("cancelled")
        return 1
    x, y, w, h = region
    print(f"JPTUTOR_REGION={x},{y},{w},{h}")
    return 0


def cmd_voices(args) -> int:
    """List available edge-tts voices for a language prefix."""
    import asyncio

    import edge_tts

    voices = asyncio.run(edge_tts.list_voices())
    for v in voices:
        if v["Locale"].lower().startswith(args.locale.lower()):
            print(f"{v['ShortName']:32} {v['Gender']:8} {v['Locale']}")
    return 0


def cmd_say(args) -> int:
    """Speak arbitrary text in Japanese or English to test audio."""
    from .script import Utterance
    from .tts import make_speaker

    settings = _settings(args)
    make_speaker(settings, "edge").speak(Utterance(args.lang, args.text, slow=args.slow))
    return 0


def cmd_doctor(args) -> int:
    """Check that the pieces needed to run are present."""
    import os
    import shutil

    from .tts import find_player

    settings = _settings(args)
    ok = True

    def row(label, good, detail):
        nonlocal ok
        ok = ok and good
        print(f"  [{'ok' if good else '!!'}] {label}: {detail}")

    print("Claude access")
    claude_bin = shutil.which("claude")
    row("Claude Code CLI", bool(claude_bin), claude_bin or "not on PATH (needed for the subscription backend)")
    row("ANTHROPIC_API_KEY", True, "set (api backend available)" if os.environ.get("ANTHROPIC_API_KEY") else "not set (fine if you use your subscription)")
    try:
        from .backends import resolve_backend

        row("backend", True, f"{resolve_backend(settings)} (JPTUTOR_BACKEND={settings.backend})")
    except SystemExit as e:
        row("backend", False, str(e).splitlines()[0])

    print("Audio")
    try:
        import pygame  # type: ignore  # noqa: F401

        row("player", True, "pygame")
    except ImportError:
        cmd = find_player()
        row("player", cmd is not None, cmd[0] if cmd else "none found: pip install pygame, or install ffmpeg / mpg123 / mpv")
    try:
        import edge_tts  # noqa: F401

        row("edge-tts", True, f"voices {settings.ja_voice} / {settings.en_voice}")
    except ImportError:
        row("edge-tts", False, "pip install edge-tts")

    print("Screen")
    try:
        import mss

        with mss.mss() as sct:
            mons = sct.monitors[1:]
        row("capture", True, f"{len(mons)} monitor(s): " + ", ".join(f"{m['width']}x{m['height']}" for m in mons))
    except Exception as e:  # no display, etc.
        row("capture", False, f"{type(e).__name__}: {e}")
    row("region", True, f"{settings.region}" if settings.region else "not set: whole primary monitor (use --manual or set JPTUTOR_REGION)")
    print("\nall good" if ok else "\nfix the items marked !! above")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jptutor", description="AI Japanese tutor for video games.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, capture=False):
        sp.add_argument("--level", choices=["beginner", "intermediate", "advanced"])
        sp.add_argument("--model", help="tutor model id (default claude-opus-5)")
        sp.add_argument("--backend", choices=["auto", "api", "claude-code"], help="api = ANTHROPIC_API_KEY, claude-code = your Claude subscription via `claude -p` (default: auto)")
        sp.add_argument("--context", default="", help="game name or scene, passed to the tutor")
        sp.add_argument("--quick", action="store_true", help="Japanese + English only, skip the breakdown")
        sp.add_argument("--dry-run", action="store_true", help="print the lesson instead of speaking it")
        sp.add_argument("--offline", action="store_true", help="use a canned sample lesson instead of calling Claude (demo)")
        if capture:
            sp.add_argument("--region", help="x,y,width,height of the dialogue box")

    sp = sub.add_parser("teach", help="teach one or more sentences given as text")
    common(sp)
    sp.add_argument("sentence", nargs="+")
    sp.add_argument("--show", action="store_true", help="also print the lesson JSON")
    sp.set_defaults(func=cmd_teach)

    sp = sub.add_parser("image", help="run the pipeline on screenshot files")
    common(sp, capture=True)
    sp.add_argument("image", nargs="+")
    sp.set_defaults(func=cmd_image)

    sp = sub.add_parser("watch", help="watch the screen while you play")
    common(sp, capture=True)
    sp.add_argument("--manual", action="store_true", help="capture on Enter instead of automatically")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("snapshot", help="save a screenshot to find the dialogue box coordinates")
    sp.add_argument("--region")
    sp.add_argument("--out", default="snapshots/screen.png")
    sp.set_defaults(func=cmd_snapshot)

    sp = sub.add_parser("select-region", help="drag to select the dialogue box; prints JPTUTOR_REGION")
    sp.set_defaults(func=cmd_select_region)

    sp = sub.add_parser("doctor", help="check Claude access, audio, and screen capture")
    sp.add_argument("--backend", choices=["auto", "api", "claude-code"])
    sp.add_argument("--region")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("voices", help="list text-to-speech voices")
    sp.add_argument("--locale", default="ja")
    sp.set_defaults(func=cmd_voices)

    sp = sub.add_parser("say", help="speak some text to test audio")
    sp.add_argument("text")
    sp.add_argument("--lang", choices=["ja", "en"], default="ja")
    sp.add_argument("--slow", action="store_true")
    sp.set_defaults(func=cmd_say)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

"""Pass 2: immediate Japanese, skip/pause/repeat controls, fallback voices, auto-region."""

import io
import threading
import time

import pytest
from PIL import Image

from jptutor.config import Settings
from jptutor.controls import DEFAULT_HOTKEYS, Controls, parse_hotkeys
from jptutor.errors import FatalError
from jptutor.fake import SAMPLE_LESSON, FakeTutor
from jptutor.memory import Memory
from jptutor.pipeline import FrameWorker, TutorPipeline, box_to_region
from jptutor.script import Utterance
from jptutor.tts import ConsoleSpeaker, FallbackSpeaker


class SlowTutor(FakeTutor):
    """Takes a while, like the real thing, so we can see what plays meanwhile."""

    def teach(self, japanese, **kw):
        time.sleep(0.2)
        return super().teach(japanese, **kw)


def test_japanese_plays_while_lesson_is_generated():
    spk = ConsoleSpeaker(out=io.StringIO())
    pipe = TutorPipeline(SlowTutor(), spk, Settings(prespeak=True))
    pipe.teach_text("学校に行きます。")
    texts = [u.text for u in spk.spoken]
    assert texts[0] == "学校に行きます。" and spk.spoken[0].slow  # heard before the lesson existed
    slow_intros = [u for u in spk.spoken if u.text == "学校に行きます。" and u.slow]
    assert len(slow_intros) == 1  # the intro is not played a second time once the lesson arrives
    assert texts[1] == SAMPLE_LESSON.english


def test_prespeak_off_keeps_old_order():
    spk = ConsoleSpeaker(out=io.StringIO())
    TutorPipeline(FakeTutor(), spk, Settings(prespeak=False)).teach_text("学校に行きます。")
    assert [u.text for u in spk.spoken[:2]] == ["学校に行きます。", SAMPLE_LESSON.english]


def test_skip_abandons_lesson_but_still_remembers_it(tmp_path):
    controls = Controls()
    spoken = []

    class Spk(ConsoleSpeaker):
        def speak(self, u):
            spoken.append(u.text)
            if len(spoken) == 3:
                controls.request_skip()

    mem = Memory(tmp_path / "m.sqlite")
    pipe = TutorPipeline(FakeTutor(), Spk(out=io.StringIO()), Settings(prespeak=False), memory=mem, controls=controls)
    pipe.teach_text("学校に行きます。")
    assert len(spoken) == 3
    assert not controls.skip.is_set()  # cleared for the next lesson
    assert "学校に行きます。" in pipe.seen and mem.lookup_sentence("学校に行きます。") is not None


def test_pause_holds_between_utterances():
    controls = Controls()
    stop = threading.Event()
    controls.paused.set()
    spk = ConsoleSpeaker(out=io.StringIO())
    pipe = TutorPipeline(FakeTutor(), spk, Settings(prespeak=False), controls=controls, stop=stop)
    t = threading.Thread(target=pipe.teach_text, args=("学校に行きます。",))
    t.start()
    time.sleep(0.3)
    assert spk.spoken == []  # nothing plays while paused
    controls.toggle_pause()
    t.join(timeout=5)
    assert len(spk.spoken) > 3


def test_repeat_last_and_worker_tasks():
    spk = ConsoleSpeaker(out=io.StringIO())
    pipe = TutorPipeline(FakeTutor(), spk, Settings(prespeak=False))
    assert pipe.repeat_last() is False
    pipe.teach_text("学校に行きます。")
    n = len(spk.spoken)
    worker = FrameWorker(pipe).start()
    worker.submit_task(lambda: pipe.repeat_last(full_breakdown=False))
    for _ in range(100):  # stop() drops pending work, so wait for the task to run first
        if len(spk.spoken) > n:
            break
        time.sleep(0.02)
    worker.stop()
    assert [u.lang for u in spk.spoken[n:]] == ["ja", "en", "ja"]


def test_repeat_policy_skips_after_enough_sightings(tmp_path):
    mem = Memory(tmp_path / "m.sqlite")
    mem.record_lesson(SAMPLE_LESSON)
    settings = Settings(prespeak=False, repeat_skip_after=2)
    for expect_spoken in (True, False):  # 1st replay speaks (times_seen 1 -> 2); 2nd is skipped
        spk = ConsoleSpeaker(out=io.StringIO())
        TutorPipeline(FakeTutor(), spk, settings, memory=mem).teach_text("学校に行きます。")
        assert bool(spk.spoken) is expect_spoken
    # a replay also counts its pieces
    assert mem.summary().learning and any("学校" in p for p in mem.summary().learning)
    piece = [r for r in mem.pieces() if r["japanese"] == "学校"][0]
    assert piece["times_seen"] == 2


def test_box_to_region():
    # frame grabbed from region (100, 200, 1000, 500); box in the lower 30% of the frame
    region = box_to_region([0.1, 0.7, 0.9, 1.0], (1000, 500), (100, 200, 1000, 500), margin=0.0)
    assert region == (200, 550, 800, 150)
    # margin grows the box but never past the frame edge
    x, y, w, h = box_to_region([0.0, 0.9, 1.0, 1.0], (1000, 500), (0, 0, 1000, 500), margin=0.05)
    assert (x, y) == (0, 425) and w == 1000 and h == 75


def test_pipeline_records_detected_box():
    pipe = TutorPipeline(FakeTutor(), ConsoleSpeaker(out=io.StringIO()), Settings(prespeak=False))
    pipe.handle_frame(Image.new("RGB", (640, 360)))
    assert pipe.last_box == ([0.05, 0.7, 0.95, 0.95], (640, 360))


def test_parse_hotkeys():
    m = parse_hotkeys("skip=<ctrl>+s, bogus=<f1>")
    assert m["skip"] == "<ctrl>+s" and m["pause"] == DEFAULT_HOTKEYS["pause"] and "bogus" not in m


def test_fallback_speaker_switches_once_and_reports():
    class Bad:
        should_abort = None
        def prepare(self, script): raise RuntimeError("network down")
        def speak(self, u): raise RuntimeError("network down")
    class Good(ConsoleSpeaker):
        should_abort = None
        def describe(self): return "system voice"
    msgs = []
    good = Good(out=io.StringIO())
    fb = FallbackSpeaker(Bad(), good, on_switch=msgs.append)
    fb.speak_all([Utterance("ja", "はい"), Utterance("en", "yes")])
    assert [u.text for u in good.spoken] == ["はい", "yes"]
    assert len(msgs) == 1 and "system voice" in msgs[0]

    class Fatal(Bad):
        def prepare(self, script): raise FatalError("no player")
    with pytest.raises(FatalError):
        FallbackSpeaker(Fatal(), good).speak_all([Utterance("ja", "はい")])


def test_make_speaker_auto_without_system_voice(monkeypatch, tmp_path):
    import shutil
    from jptutor import tts

    monkeypatch.setattr(shutil, "which", lambda name: None)  # no say / espeak / players
    monkeypatch.setattr(tts, "Player", lambda: type("P", (), {"describe": lambda self: "fake", "play": lambda *a, **k: None})())
    spk = tts.make_speaker(Settings(cache_dir=tmp_path), "auto")
    assert isinstance(spk, tts.EdgeSpeaker)  # falls back to plain edge when no OS voice exists

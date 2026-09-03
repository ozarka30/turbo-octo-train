"""Pass-1 fixes from the review: nothing is marked done until it is done, fatal errors stop the session."""

import io
import json
import threading

import pytest
from PIL import Image

from jptutor.cache import OcrCache, png_bytes
from jptutor.cli import load_dotenv
from jptutor.config import Settings
from jptutor.errors import FatalError
from jptutor.fake import SAMPLE_LESSON, FakeTutor
from jptutor.lesson import OcrLine, OcrResult, split_sentences
from jptutor.memory import Memory
from jptutor.pipeline import FrameWorker, TutorPipeline
from jptutor.tts import ConsoleSpeaker


class FlakyTutor(FakeTutor):
    def __init__(self, fail_times=1, exc=RuntimeError("api down")):
        super().__init__()
        self.fail_times, self.exc = fail_times, exc

    def teach(self, japanese, **kw):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.exc
        return super().teach(japanese, **kw)


def test_failed_lesson_is_retried_not_marked_seen(tmp_path):
    mem = Memory(tmp_path / "m.sqlite")
    tutor = FlakyTutor(fail_times=1)
    pipe = TutorPipeline(tutor, ConsoleSpeaker(out=io.StringIO()), Settings(), memory=mem)
    with pytest.raises(RuntimeError):
        pipe.teach_text("学校に行きます。")
    assert "学校に行きます。" not in pipe.seen and mem.lookup_sentence("学校に行きます。") is None
    assert pipe.teach_text("学校に行きます。") is not None  # second try works and is now remembered
    assert "学校に行きます。" in pipe.seen and mem.lookup_sentence("学校に行きます。") is not None


def test_failed_speech_is_not_recorded(tmp_path):
    class BrokenSpeaker(ConsoleSpeaker):
        def speak(self, u):
            raise RuntimeError("tts down")

    mem = Memory(tmp_path / "m.sqlite")
    pipe = TutorPipeline(FakeTutor(), BrokenSpeaker(out=io.StringIO()), Settings(), memory=mem)
    with pytest.raises(RuntimeError):
        pipe.teach_text("学校に行きます。")
    assert mem.lookup_sentence("学校に行きます。") is None and pipe.lessons == []


def test_multi_sentence_box_survives_one_failure():
    tutor = FlakyTutor(fail_times=1)
    pipe = TutorPipeline(tutor, ConsoleSpeaker(out=io.StringIO()), Settings())
    taught = pipe.teach_line("一つ。二つ。三つ。")
    assert [l.japanese for l in taught] == ["二つ。", "三つ。"]
    assert "一つ。" not in pipe.seen  # the failed one can come back


def test_memory_key_is_the_ocr_text(tmp_path):
    """Claude may echo the sentence with different punctuation; lookup must still hit."""
    mem = Memory(tmp_path / "m.sqlite")

    class Normalising(FakeTutor):
        def teach(self, japanese, **kw):
            return SAMPLE_LESSON.model_copy(update={"japanese": "学校に行きます"})  # dropped the 。

    pipe = TutorPipeline(Normalising(), ConsoleSpeaker(out=io.StringIO()), Settings(), memory=mem)
    pipe.teach_text("学校に行きます。")
    assert mem.lookup_sentence("学校に行きます。") is not None


def test_skip_policy_does_not_bump_sightings(tmp_path):
    mem = Memory(tmp_path / "m.sqlite")
    mem.record_lesson(SAMPLE_LESSON)
    pipe = TutorPipeline(FakeTutor(), ConsoleSpeaker(out=io.StringIO()), Settings(repeat="skip"), memory=mem)
    pipe.teach_text("学校に行きます。")
    assert mem.lookup_sentence("学校に行きます。").times_seen == 1


def test_fatal_error_stops_worker_and_is_reraised():
    tutor = FlakyTutor(fail_times=5, exc=FatalError("not logged in"))
    from jptutor.display import ConsoleDisplay

    disp = ConsoleDisplay(out=io.StringIO())
    pipe = TutorPipeline(tutor, ConsoleSpeaker(out=io.StringIO()), Settings(), display=disp)
    worker = FrameWorker(pipe).start()
    worker.submit(Image.new("RGB", (10, 10)))
    worker._thread.join(timeout=5)
    assert pipe.stop.is_set() and disp.errors == ["not logged in"]
    with pytest.raises(FatalError):
        worker.stop()


def test_stop_event_cuts_a_lesson_short():
    stop = threading.Event()
    spoken = []

    class Spk(ConsoleSpeaker):
        def speak(self, u):
            spoken.append(u.text)
            if len(spoken) == 2:
                stop.set()

    pipe = TutorPipeline(FakeTutor(), Spk(out=io.StringIO()), Settings(), stop=stop)
    pipe.teach_text("学校に行きます。")
    assert len(spoken) == 2


def test_split_keeps_closing_quotes():
    assert split_sentences("「行くぞ！」と言った。") == ["「行くぞ！」と言った。"]
    assert split_sentences("行くぞ！　行こう。") == ["行くぞ！", "行こう。"]


def test_ocr_cache_skips_half_typed_frames(tmp_path):
    cache = OcrCache(tmp_path, "m")
    png = png_bytes(Image.new("RGB", (8, 8)))
    cache.put(png, OcrResult(lines=[OcrLine(text="近づ", kind="dialogue", complete=False)]))
    assert cache.get(png) is None
    cache.put(png, OcrResult(lines=[OcrLine(text="近づくな。", kind="dialogue")]))
    assert cache.get(png).lines[0].text == "近づくな。"


def test_load_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nJPTUTOR_LEVEL=advanced\nexport JPTUTOR_REGION=\"1,2,3,4\"\nANTHROPIC_API_KEY=keep\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "already")
    monkeypatch.delenv("JPTUTOR_LEVEL", raising=False)
    monkeypatch.delenv("JPTUTOR_REGION", raising=False)
    assert load_dotenv(env) == 2
    import os

    assert os.environ["JPTUTOR_LEVEL"] == "advanced" and os.environ["JPTUTOR_REGION"] == "1,2,3,4"
    assert os.environ["ANTHROPIC_API_KEY"] == "already"  # never overrides

from PIL import Image

from jptutor.config import Settings
from jptutor.fake import FakeTutor
from jptutor.lesson import OcrLine, contains_japanese
from jptutor.pipeline import FrameWorker, SeenLines, TutorPipeline, normalize, pick_lines
from jptutor.tts import ConsoleSpeaker


def test_contains_japanese():
    assert contains_japanese("学校")
    assert contains_japanese("いきます")
    assert contains_japanese("セーブ")
    assert not contains_japanese("HP 120/120")


def test_normalize_ignores_width_and_whitespace():
    assert normalize("学校に 行きます") == normalize("学校に行きます")
    assert normalize("ＨＰ") == normalize("HP")


def test_seen_lines_dedupes_and_is_bounded():
    seen = SeenLines(capacity=2)
    assert seen.add("a") and not seen.add("a")
    seen.add("b")
    seen.add("c")  # evicts "a"
    assert "a" not in seen and "c" in seen


def test_pick_lines_drops_ui_and_non_japanese():
    lines = [
        OcrLine(text="学校に行きます。", kind="dialogue"),
        OcrLine(text="HP 120/120", kind="system"),
        OcrLine(text="セーブ", kind="menu"),
        OcrLine(text="Press A", kind="dialogue"),
        OcrLine(text="……", kind="narration"),
    ]
    assert [l.text for l in pick_lines(lines)] == ["学校に行きます。"]


def test_pipeline_teaches_new_dialogue_once():
    tutor = FakeTutor()
    speaker = ConsoleSpeaker(out=open("/dev/null", "w"))
    pipe = TutorPipeline(tutor, speaker, Settings(), context="test game")
    frame = Image.new("RGB", (100, 40))
    assert len(pipe.handle_frame(frame)) == 1
    assert len(pipe.handle_frame(frame)) == 0  # same text again -> skipped
    assert tutor.teach_calls == ["学校に行きます。"]
    assert pipe.history  # chunks remembered for the next lesson
    assert any(u.lang == "ja" for u in speaker.spoken) and any(u.lang == "en" for u in speaker.spoken)


def test_frame_worker_drops_oldest_when_full():
    tutor = FakeTutor()
    pipe = TutorPipeline(tutor, ConsoleSpeaker(out=open("/dev/null", "w")), Settings())
    worker = FrameWorker(pipe, max_queue=1)  # not started: queue fills up
    worker.submit(Image.new("RGB", (10, 10)))
    worker.submit(Image.new("RGB", (10, 10)))
    assert worker.dropped == 1 and worker.q.qsize() == 1

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


def test_split_sentences():
    from jptutor.lesson import split_sentences

    assert split_sentences("この街には近づくな。危険だ。") == ["この街には近づくな。", "危険だ。"]
    assert split_sentences("はい") == ["はい"]
    assert split_sentences("何？　まさか！") == ["何？", "まさか！"]


def test_multi_sentence_box_teaches_each_sentence_with_context():
    tutor = FakeTutor(ocr_lines=[OcrLine(text="この街には近づくな。危険だ。", kind="dialogue", speaker="ユウ")])
    pipe = TutorPipeline(tutor, ConsoleSpeaker(out=open("/dev/null", "w")), Settings())
    taught = pipe.handle_frame(Image.new("RGB", (10, 10)))
    assert [l.japanese for l in taught] == ["この街には近づくな。", "危険だ。"]
    assert pipe.handle_frame(Image.new("RGB", (10, 10))) == []  # whole box remembered


def test_incomplete_and_choice_lines():
    lines = [
        OcrLine(text="この街には近づ", kind="dialogue", complete=False),
        OcrLine(text="彼を助ける", kind="choice"),
    ]
    assert [l.text for l in pick_lines(lines)] == ["彼を助ける"]


def test_tutor_user_prompt_mentions_box_only_when_multi_sentence():
    from jptutor.prompts import build_tutor_user

    single = build_tutor_user("はい。", speaker="A", context="G")
    assert "Whole dialogue box" not in single and "Sentence to teach: はい。" in single
    multi = build_tutor_user("危険だ。", full_line="近づくな。危険だ。", history=["Sentence: x = y"])
    assert "Whole dialogue box: 近づくな。危険だ。" in multi and "Sentence: x = y" in multi

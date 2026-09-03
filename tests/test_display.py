import io

from jptutor.config import Settings
from jptutor.display import ConsoleDisplay, DisplaySpeaker
from jptutor.fake import SAMPLE_LESSON, FakeTutor
from jptutor.lesson import BuildStep, Chunk, Lesson
from jptutor.pipeline import TutorPipeline
from jptutor.script import build_script, chunk_spans, find_span, mark_span
from jptutor.tts import ConsoleSpeaker


def test_chunk_spans_follow_order_and_handle_repeats():
    lesson = Lesson(
        japanese="私は私の家に帰る。", reading="", english="", literal="",
        chunks=[Chunk(japanese="私", reading="わたし", meaning="I"), Chunk(japanese="は", reading="わ", meaning="topic"),
                Chunk(japanese="私の", reading="わたしの", meaning="my"), Chunk(japanese="家", reading="いえ", meaning="home"),
                Chunk(japanese="に", reading="に", meaning="to"), Chunk(japanese="帰る", reading="かえる", meaning="return")],
        build_up=[], pattern="",
    )
    assert chunk_spans(lesson) == [(0, 1), (1, 2), (2, 4), (4, 5), (5, 6), (6, 8)]


def test_find_span_ignores_trailing_punctuation_and_missing():
    assert find_span("この街には近づくな。", "近づくな。") == (5, 9)
    assert find_span("この街には近づくな。", "「この街」") == (0, 3)
    assert find_span("この街には近づくな。", "学校") is None


def test_script_carries_spans_and_readings():
    s = build_script(SAMPLE_LESSON)
    assert s[0].span == (0, len(SAMPLE_LESSON.japanese))
    chunk_ja = [u for u in s if u.lang == "ja" and u.slow and u.reading]
    assert [u.span for u in chunk_ja] == [(0, 2), (2, 3), (3, 7)]
    assert chunk_ja[0].reading == "がっこう"
    # the chunk's English explanation keeps the same highlight
    idx = s.index(chunk_ja[0])
    assert s[idx + 1].lang == "en" and s[idx + 1].span == (0, 2)
    # questions carry no highlight, answers do
    q = next(u for u in s if u.text.startswith("So how would you say"))
    assert q.span is None and s[s.index(q) + 1].span == (0, 3)


def test_mark_span():
    assert mark_span("学校に行きます。", (2, 3)) == "学校【に】行きます。"
    assert mark_span("学校に行きます。", None) == "学校に行きます。"


def test_console_display_prints_highlights_in_sync_with_speech():
    out = io.StringIO()
    display = ConsoleDisplay(out=out)
    speaker = ConsoleSpeaker(out=io.StringIO())
    pipe = TutorPipeline(FakeTutor(), speaker, Settings(), display=display)
    pipe.teach_text("学校に行きます。")
    text = out.getvalue()
    assert "┌ 学校に行きます。" in text
    assert "【学校】に行きます。  がっこう" in text
    assert "学校【に】行きます。  に" in text
    assert len(display.frames) == len(speaker.spoken)  # one highlight per utterance


def test_display_speaker_prepares_then_speaks_in_order():
    events = []

    class Spk:
        def prepare(self, script):
            events.append("prepare")

        def speak(self, u):
            events.append(("speak", u.text))

    class Disp:
        def show_lesson(self, lesson): ...
        def on_utterance(self, u):
            events.append(("show", u.text))
        def finish(self): ...

    ds = DisplaySpeaker(Spk(), Disp())
    ds.speak_all(build_script(SAMPLE_LESSON, full_breakdown=False))
    assert events[0] == "prepare"
    assert events[1:5] == [("show", SAMPLE_LESSON.japanese), ("speak", SAMPLE_LESSON.japanese), ("show", SAMPLE_LESSON.english), ("speak", SAMPLE_LESSON.english)]

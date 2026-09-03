import io

from jptutor.config import Settings
from jptutor.fake import SAMPLE_LESSON, FakeTutor
from jptutor.lesson import Chunk
from jptutor.memory import KNOWN_AFTER, Memory
from jptutor.pipeline import TutorPipeline
from jptutor.tts import ConsoleSpeaker


def other_lesson():
    return SAMPLE_LESSON.model_copy(update={
        "japanese": "駅に行きます。", "english": "I'm going to the station.", "pattern": "Same shape: place, ni, verb.",
        "chunks": [Chunk(japanese="駅", reading="えき", meaning="station"), Chunk(japanese="に", reading="に", meaning="to, toward"),
                   Chunk(japanese="行きます", reading="いきます", meaning="go, polite form")],
    })


def test_record_lookup_and_tiers(tmp_path):
    mem = Memory(tmp_path / "m.sqlite")
    assert mem.lookup_sentence("学校に行きます。") is None
    mem.record_lesson(SAMPLE_LESSON, game="G", now=1.0)
    stored = mem.lookup_sentence("学校に 行きます。")  # whitespace-insensitive
    assert stored and stored.english == SAMPLE_LESSON.english and stored.times_seen == 1

    for i in range(KNOWN_AFTER - 1):
        mem.record_lesson(other_lesson(), now=2.0 + i)
    summary = mem.summary()
    known = " ".join(summary.known)
    assert "に (to, toward)" in known and "行きます" in known  # seen 3 times
    assert any(s.startswith("学校") for s in summary.learning) and any(s.startswith("駅") for s in summary.learning)
    assert len(summary.patterns) == 2
    assert summary.recent_sentences[-1].startswith("駅に行きます。")
    text = summary.render()
    assert "Known well" in text and "Met once or twice" in text and "Patterns already taught" in text

    st = mem.stats()
    assert st["sentences"] == 2 and st["pieces"] == 4 and st["pieces_known"] == 2 and st["games"] == ["G"]


def test_empty_memory_renders_first_lesson(tmp_path):
    assert "first lesson" in Memory(tmp_path / "m.sqlite").summary().render()


def test_pipeline_replays_known_line_without_calling_claude(tmp_path):
    mem = Memory(tmp_path / "m.sqlite")
    tutor = FakeTutor()
    settings = Settings(repeat="quick")
    pipe = TutorPipeline(tutor, ConsoleSpeaker(out=io.StringIO()), settings, memory=mem)
    pipe.teach_text("学校に行きます。")
    assert tutor.teach_calls == ["学校に行きます。"]

    # next session: same line comes up again
    tutor2 = FakeTutor()
    spk = ConsoleSpeaker(out=io.StringIO())
    pipe2 = TutorPipeline(tutor2, spk, settings, memory=mem)
    lesson = pipe2.teach_text("学校に行きます。")
    assert lesson is not None and tutor2.teach_calls == [] and pipe2.replayed == 1
    assert [u.lang for u in spk.spoken] == ["ja", "en", "ja"]  # quick pass only
    assert mem.lookup_sentence("学校に行きます。").times_seen == 2


def test_pipeline_repeat_skip_and_full(tmp_path):
    mem = Memory(tmp_path / "m.sqlite")
    mem.record_lesson(SAMPLE_LESSON)
    tutor = FakeTutor()
    pipe = TutorPipeline(tutor, ConsoleSpeaker(out=io.StringIO()), Settings(repeat="skip"), memory=mem)
    assert pipe.teach_text("学校に行きます。") is None and tutor.teach_calls == []
    pipe = TutorPipeline(tutor, ConsoleSpeaker(out=io.StringIO()), Settings(repeat="full"), memory=mem)
    assert pipe.teach_text("学校に行きます。") is not None and tutor.teach_calls == ["学校に行きます。"]


def test_knowledge_reaches_the_tutor(tmp_path):
    mem = Memory(tmp_path / "m.sqlite")
    mem.record_lesson(SAMPLE_LESSON)
    seen = {}

    class Spy(FakeTutor):
        def teach(self, japanese, **kw):
            seen.update(kw)
            return super().teach(japanese, **kw)

    pipe = TutorPipeline(Spy(), ConsoleSpeaker(out=io.StringIO()), Settings(), memory=mem)
    pipe.teach_text("駅に行きます。")
    assert "学校 (school)" in seen["knowledge"] and "学校に行きます。 = I'm going to school." in seen["knowledge"]


def test_knowledge_snapshot_is_stable_between_refreshes(tmp_path):
    """The cached block must not change every lesson; the delta carries the rest."""
    mem = Memory(tmp_path / "m.sqlite")
    calls = []

    class Spy(FakeTutor):
        def teach(self, japanese, **kw):
            calls.append((kw["knowledge"], kw["recent"]))
            return super().teach(japanese, **kw)

    pipe = TutorPipeline(Spy(), ConsoleSpeaker(out=io.StringIO()), Settings(knowledge_refresh=3), memory=mem)
    for i in range(5):
        pipe.teach_text(f"文{i}。")
    snapshots = [k for k, _ in calls]
    assert snapshots[0] == snapshots[1] == snapshots[2]  # lessons 1-3 share one snapshot
    assert snapshots[3] != snapshots[0] and snapshots[3] == snapshots[4]  # refreshed at lesson 4
    assert calls[0][1] == "" and "文0。" in calls[1][1] and "文1。" in calls[2][1]
    assert calls[3][1] == "" and "文3。" in calls[4][1]  # delta resets with the snapshot


def test_usage_persisted_in_memory(tmp_path):
    from jptutor.usage import Call

    mem = Memory(tmp_path / "m.sqlite")
    mem.record_usage(Call(backend="api", model="claude-opus-5", kind="lesson", input_tokens=100, cache_read=900, cache_write=0, output_tokens=50, cost_usd=0.01))
    mem.record_usage(Call(backend="api", model="claude-opus-5", kind="ocr", input_tokens=1000, cache_read=0, cache_write=0, output_tokens=20, cost_usd=None))
    u = mem.usage_totals()
    assert u["calls"] == 2 and u["cache_read"] == 900 and u["priced"] == 1 and abs(u["cost"] - 0.01) < 1e-9
    assert round(u["cached_pct"]) == 45


def test_export_anki(tmp_path):
    mem = Memory(tmp_path / "m.sqlite")
    mem.record_lesson(SAMPLE_LESSON)
    out = tmp_path / "anki.txt"
    assert mem.export_anki(out) == 3
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("学校\tがっこう<br>school")
    assert "学校に行きます。" in lines[0]


def test_forget(tmp_path):
    mem = Memory(tmp_path / "m.sqlite")
    mem.record_lesson(SAMPLE_LESSON)
    mem.forget()
    assert mem.stats()["sentences"] == 0 and mem.lookup_sentence("学校に行きます。") is None


def test_settings_memory_env(tmp_path):
    assert Settings.from_env({"JPTUTOR_MEMORY": "0"}).memory_path is None
    s = Settings.from_env({"JPTUTOR_MEMORY": str(tmp_path / "x.sqlite"), "JPTUTOR_REPEAT": "skip"})
    assert s.memory_path == tmp_path / "x.sqlite" and s.repeat == "skip"

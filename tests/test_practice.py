import io

import numpy as np

from jptutor.config import Settings
from jptutor.controls import Controls
from jptutor.display import ConsoleDisplay
from jptutor.fake import SAMPLE_LESSON, FakeTutor
from jptutor.pipeline import TutorPipeline
from jptutor.practice import Practice, feedback, piece_for, score_attempt, to_kana
from jptutor.tts import ConsoleSpeaker


def test_to_kana_normalises():
    assert to_kana("学校に行きます。") == "がっこうにいきます"
    assert to_kana("コンビニ　に") == "こんびにに"  # katakana and spaces


def test_score_tiers():
    great = score_attempt("学校に行きます。", "がっこうにいきます", "学校に行きます")
    assert great.score == 1.0 and great.tier == "great" and great.misses == []
    close = score_attempt("学校に行きます。", "がっこうにいきます", "がっこにいきます")
    assert close.tier == "close" and close.misses == [("う", "")]
    off = score_attempt("学校に行きます。", "がっこうにいきます", "駅に帰ります")
    assert off.tier == "off" and off.score < 0.7
    assert score_attempt("学校に行きます。", "がっこうにいきます", "").tier == "silent"


def test_score_accepts_reading_spelled_as_pronounced():
    # the lesson writes the topic particle as わ; whisper + kakasi produce は. Both must pass.
    a = score_attempt("この街には近づくな。", "このまちにわちかづくな", "この街には近づくな")
    assert a.score == 1.0


def test_feedback_points_at_the_piece():
    attempt = score_attempt("学校に行きます。", "がっこうにいきます", "がっこうにきます")  # dropped い of いきます
    assert attempt.tier == "close"
    assert piece_for(SAMPLE_LESSON, attempt.misses[0][0]) == ("行きます", "いきます", "go, polite form")
    utts = feedback(SAMPLE_LESSON, attempt)
    assert utts[0].text == "Close."
    assert "go, polite form" in utts[1].text and utts[1].span == (3, 7)
    assert utts[2].lang == "ja" and utts[2].text == "いきます" and utts[2].slow
    assert utts[-1].lang == "ja" and utts[-1].text == SAMPLE_LESSON.japanese
    great = feedback(SAMPLE_LESSON, score_attempt("学校に行きます。", "がっこうにいきます", "学校に行きます"))
    assert len(great) == 1 and great[0].text.startswith("Very close")


class FakeRecorder:
    def __init__(self):
        self.calls = 0

    def record(self, **kw):
        self.calls += 1
        return np.ones(1600, dtype="float32")


class FakeTranscriber:
    def __init__(self, text):
        self.text = text

    def transcribe(self, audio):
        return self.text if len(audio) else ""


def make_pipeline(settings, heard):
    rec = FakeRecorder()
    practice = Practice(rec, FakeTranscriber(heard))
    spk = ConsoleSpeaker(out=io.StringIO())
    disp = ConsoleDisplay(out=io.StringIO())
    pipe = TutorPipeline(FakeTutor(), spk, settings, display=disp, practice=practice, controls=Controls())
    return pipe, spk, disp, rec


def test_practice_after_lesson_in_auto_mode():
    pipe, spk, disp, rec = make_pipeline(Settings(prespeak=False, practice="auto"), "学校に行きます")
    pipe.teach_text("学校に行きます。")
    assert rec.calls == 1
    texts = [u.text for u in spk.spoken]
    assert "Your turn. Say the whole line." in texts and "Very close. That is it." in texts
    assert "🎤 heard: がっこうにいきます  match 100%" in disp.out.getvalue()


def test_practice_only_on_hotkey_by_default():
    pipe, spk, disp, rec = make_pipeline(Settings(prespeak=False), "学校に行きます")
    pipe.teach_text("学校に行きます。")
    assert rec.calls == 0
    assert pipe.practice_last() is True and rec.calls == 1


def test_practice_before_any_lesson_is_a_noop():
    pipe, *_ = make_pipeline(Settings(prespeak=False), "x")
    assert pipe.practice_last() is False


def test_practice_failure_is_reported_not_fatal():
    class BadRecorder:
        def record(self, **kw):
            raise OSError("no input device")

    spk = ConsoleSpeaker(out=io.StringIO())
    disp = ConsoleDisplay(out=io.StringIO())
    pipe = TutorPipeline(FakeTutor(), spk, Settings(prespeak=False, practice="auto"), display=disp, practice=Practice(BadRecorder(), FakeTranscriber("x")))
    assert pipe.teach_text("学校に行きます。") is not None
    assert disp.errors and "microphone" in disp.errors[0]


def test_practice_settings_env():
    s = Settings.from_env({"JPTUTOR_PRACTICE": "auto", "JPTUTOR_WHISPER_MODEL": "base", "JPTUTOR_MIC_THRESHOLD": "0.02"})
    assert s.practice == "auto" and s.whisper_model == "base" and s.mic_threshold == 0.02

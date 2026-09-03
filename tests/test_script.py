from jptutor.fake import SAMPLE_LESSON
from jptutor.script import build_script, script_as_text


def test_script_order_hear_then_understand_then_break_down():
    s = build_script(SAMPLE_LESSON)
    assert s[0].lang == "ja" and s[0].slow and s[0].text == SAMPLE_LESSON.japanese
    assert s[1].lang == "en" and s[1].text == SAMPLE_LESSON.english
    assert s[2].text == "Let's break that down."
    # each chunk: japanese reading then english meaning+note
    assert s[3].lang == "ja" and s[3].text == "がっこう"
    assert s[4].lang == "en" and s[4].text.startswith("school. Gakkou.")


def test_script_build_up_has_thinking_pause_and_ends_with_full_line():
    s = build_script(SAMPLE_LESSON, quiz_pause=3.0)
    prompts = [u for u in s if u.text.startswith("So how would you say")]
    assert prompts and prompts[0].pause_after == 3.0
    assert s[-2].lang == "ja" and s[-2].text == SAMPLE_LESSON.japanese
    assert s[-1].lang == "en" and s[-1].text == SAMPLE_LESSON.english


def test_quick_mode_is_ja_en_ja_only():
    s = build_script(SAMPLE_LESSON, full_breakdown=False)
    assert [u.lang for u in s] == ["ja", "en", "ja"]


def test_script_as_text_marks_language():
    text = script_as_text(build_script(SAMPLE_LESSON, full_breakdown=False))
    assert text.splitlines()[0].startswith("[ja slow]")

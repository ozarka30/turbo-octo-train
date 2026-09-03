"""The `claude -p` backend, with the subprocess mocked."""

import json
import shutil

import pytest
from PIL import Image

from jptutor.backends import resolve_backend
from jptutor.claude_code_backend import ClaudeCodeError, ClaudeCodeTutor
from jptutor.errors import FatalError
from jptutor.config import Settings
from jptutor.fake import SAMPLE_LESSON
from jptutor.prompts import LEVEL_GUIDANCE


class Proc:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def make(tmp_path, monkeypatch, stdout, returncode=0):
    calls = []

    def runner(args, **kw):
        calls.append((args, kw))
        return Proc(stdout, returncode)

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")
    settings = Settings(cache_dir=tmp_path, level="beginner", tutor_effort="medium")
    return ClaudeCodeTutor(settings, runner=runner), calls


def envelope(payload):
    return json.dumps({
        "type": "result", "is_error": False, "result": "done", "structured_output": payload, "session_id": "s",
        "total_cost_usd": 0.0421,
        "usage": {"input_tokens": 12, "cache_creation_input_tokens": 900, "cache_read_input_tokens": 3000, "output_tokens": 700},
    })


def test_teach_builds_headless_command(tmp_path, monkeypatch):
    tutor, calls = make(tmp_path, monkeypatch, envelope(SAMPLE_LESSON.model_dump()))
    lesson = tutor.teach("学校に行きます。", speaker="ユウ", context="Pokemon", knowledge="学校 = school", recent="駅 = station")
    assert lesson.english == SAMPLE_LESSON.english

    args, kw = calls[0]
    assert args[0].endswith("claude") and args[1] == "-p"
    assert kw["encoding"] == "utf-8"
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--model") + 1] == "claude-opus-5"
    assert args[args.index("--effort") + 1] == "medium"
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert "--no-session-persistence" in args
    assert args[args.index("--allowedTools") + 1] == "StructuredOutput"
    i = args.index("--disallowedTools")
    assert "Bash" in args[i + 1:] and "StructuredOutput" not in args[i + 1:]
    schema = json.loads(args[args.index("--json-schema") + 1])
    assert "chunks" in schema["properties"]
    system = args[args.index("--system-prompt") + 1]
    assert LEVEL_GUIDANCE["beginner"] in system and "学校 = school" in system  # memory rides in the cached system prompt
    prompt = kw["input"]
    assert args[-1] != prompt  # prompt travels on stdin, not as a positional
    assert "学校に行きます。" in prompt and "ユウ" in prompt and "駅 = station" in prompt
    assert kw["cwd"] == str(tmp_path / "claude-work")


def test_ocr_saves_png_in_workdir_and_allows_read(tmp_path, monkeypatch):
    ocr_payload = {"lines": [{"text": "はい", "kind": "dialogue", "speaker": ""}]}
    tutor, calls = make(tmp_path, monkeypatch, envelope(ocr_payload))
    seen = {}

    real_runner = tutor._run

    def runner(args, **kw):
        # the screenshot must exist while claude runs
        pngs = list((tmp_path / "claude-work").glob("shot-*.png"))
        seen["pngs"] = pngs
        return real_runner(args, **kw)

    tutor._run = runner
    result = tutor.ocr(Image.new("RGB", (20, 10), "white"))
    assert result.lines[0].text == "はい"
    assert len(seen["pngs"]) == 1
    assert not seen["pngs"][0].exists()  # cleaned up afterwards
    args, _ = calls[0]
    i = args.index("--allowedTools")
    assert args[i + 1:i + 3] == ["Read", "StructuredOutput"]
    assert seen["pngs"][0].name in calls[0][1]["input"]


def test_falls_back_to_result_text_and_reports_errors(tmp_path, monkeypatch):
    tutor, _ = make(tmp_path, monkeypatch, json.dumps({"is_error": False, "result": SAMPLE_LESSON.model_dump_json()}))
    assert tutor.teach("x").japanese == SAMPLE_LESSON.japanese

    tutor, _ = make(tmp_path, monkeypatch, json.dumps({"is_error": True, "result": "Something odd happened"}))
    with pytest.raises(ClaudeCodeError, match="Something odd"):
        tutor.teach("x")

    tutor, _ = make(tmp_path, monkeypatch, "", returncode=1)
    with pytest.raises(ClaudeCodeError, match="exit 1"):
        tutor.teach("x")


def test_claude_code_usage_is_recorded(tmp_path, monkeypatch):
    from jptutor.usage import get_meter

    get_meter().calls.clear()
    tutor, _ = make(tmp_path, monkeypatch, envelope(SAMPLE_LESSON.model_dump()))
    tutor.teach("x")
    call = get_meter().calls[-1]
    assert call.backend == "claude-code" and call.cache_read == 3000 and call.cost_usd == 0.0421
    assert "77% served from cache" in get_meter().summary()


def test_missing_binary_gives_install_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    tutor = ClaudeCodeTutor(Settings(cache_dir=tmp_path), binary="claude-nope")
    with pytest.raises(FatalError, match="Claude Code CLI not found"):
        tutor.teach("x")


def test_not_logged_in_is_fatal(tmp_path, monkeypatch):
    tutor, _ = make(tmp_path, monkeypatch, json.dumps({"is_error": True, "result": "Not logged in. Please run /login"}))
    with pytest.raises(FatalError, match="not logged in"):
        tutor.teach("x")


def test_resolve_backend_auto(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert resolve_backend(Settings(), env={"ANTHROPIC_API_KEY": "k"}) == "api"
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")
    assert resolve_backend(Settings(), env={}) == "claude-code"
    assert resolve_backend(Settings(backend="api"), env={}) == "api"
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="No way to reach Claude"):
        resolve_backend(Settings(), env={})

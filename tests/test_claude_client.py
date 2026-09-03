"""Exercise the real SDK call path against a mock HTTP transport (no network)."""

import json

import anthropic
import httpx2 as httpx
from PIL import Image

from jptutor.claude_client import ClaudeTutor, FALLBACK_BETA
from jptutor.config import Settings
from jptutor.fake import SAMPLE_LESSON


def make_client(captured, body_text):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [{"type": "text", "text": body_text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    return anthropic.Anthropic(
        api_key="test",
        base_url="http://mock.local",
        http_client=anthropic.DefaultHttpxClient(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )


def test_teach_request_shape_and_parsing():
    captured = {}
    tutor = ClaudeTutor(Settings(level="intermediate"), client=make_client(captured, SAMPLE_LESSON.model_dump_json()))
    lesson = tutor.teach("学校に行きます。", speaker="ユウ", context="Pokemon", history=["学校 = school"])

    assert lesson.japanese == SAMPLE_LESSON.japanese and len(lesson.chunks) == 3
    body = captured["json"]
    assert body["model"] == "claude-opus-5"
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in captured["headers"]["anthropic-beta"]
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "intermediate" in body["system"][0]["text"]
    assert "学校 = school" in body["messages"][0]["content"]
    assert "ユウ" in body["messages"][0]["content"]


def test_ocr_sends_png_image():
    captured = {}
    ocr_json = json.dumps({"lines": [{"text": "はい", "kind": "dialogue", "speaker": ""}]})
    tutor = ClaudeTutor(Settings(), client=make_client(captured, ocr_json))
    result = tutor.ocr(Image.new("RGB", (32, 16), "white"))
    assert result.lines[0].text == "はい"
    content = captured["json"]["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    assert captured["json"]["output_config"]["effort"] == "low"

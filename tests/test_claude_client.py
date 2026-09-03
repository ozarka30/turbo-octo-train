"""Exercise the real SDK call path against a mock HTTP transport (no network)."""

import json

import anthropic
import httpx2 as httpx
from PIL import Image

from jptutor.claude_client import ClaudeTutor, FALLBACK_BETA
from jptutor.config import Settings
from jptutor.fake import SAMPLE_LESSON
from jptutor.prompts import LEVEL_GUIDANCE


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
    lesson = tutor.teach("学校に行きます。", speaker="ユウ", context="Pokemon", knowledge="学校 = school", recent="駅 = station")

    assert lesson.japanese == SAMPLE_LESSON.japanese and len(lesson.chunks) == 3
    body = captured["json"]
    assert body["model"] == "claude-opus-5"
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in captured["headers"]["anthropic-beta"]
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"
    # two cached system blocks: frozen prompt, then the memory snapshot; per-lesson delta in the user turn
    assert body["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert body["system"][1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert LEVEL_GUIDANCE["intermediate"] in body["system"][0]["text"]
    assert "学校 = school" in body["system"][1]["text"]
    assert "駅 = station" in body["messages"][0]["content"]
    assert "ユウ" in body["messages"][0]["content"]


def test_ocr_sends_png_image_and_caches_by_pixels(tmp_path):
    captured = {}
    ocr_json = json.dumps({"lines": [{"text": "はい", "kind": "dialogue", "speaker": ""}]})
    tutor = ClaudeTutor(Settings(cache_dir=tmp_path), client=make_client(captured, ocr_json))
    result = tutor.ocr(Image.new("RGB", (32, 16), "white"))
    assert result.lines[0].text == "はい"
    content = captured["json"]["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    assert captured["json"]["output_config"]["effort"] == "low"

    captured.clear()
    again = tutor.ocr(Image.new("RGB", (32, 16), "white"))  # identical pixels: served from disk
    assert again.lines[0].text == "はい" and captured == {} and tutor.ocr_cache.hits == 1
    captured.clear()
    tutor.ocr(Image.new("RGB", (32, 16), "black"))  # different frame: real call
    assert "messages" in captured["json"]


def test_usage_is_metered(tmp_path):
    from jptutor.usage import get_meter

    meter = get_meter()
    meter.calls.clear()
    captured = {}
    tutor = ClaudeTutor(Settings(cache_dir=tmp_path), client=make_client(captured, SAMPLE_LESSON.model_dump_json()))
    tutor.teach("x")
    assert len(meter.calls) == 1 and meter.calls[0].kind == "lesson" and meter.calls[0].input_tokens == 10
    assert meter.calls[0].cost_usd is not None and "1 calls" in meter.summary()

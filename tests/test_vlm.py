# tests/test_vlm.py
import json, httpx
from pathlib import Path
from PIL import Image
from receipt_evidence.vlm import LlamaServerClient, FakeVlmClient, image_content

def test_chat_sends_json_schema_and_temperature_zero():
    seen = {}
    def handler(req: httpx.Request):
        if req.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"a\":1}"}}]})
    c = LlamaServerClient("http://vlm", transport=httpx.MockTransport(handler))
    assert c.healthy()
    out = c.chat([{"role": "user", "content": "hi"}], json_schema={"type": "object"})
    assert out == '{"a":1}' and seen["temperature"] == 0
    assert seen["response_format"] == {"type": "json_schema", "json_schema": {"name": "receipt", "schema": {"type": "object"}}}

def test_chat_json_object_mode():
    seen = {}
    def handler(req):
        seen.update(json.loads(req.content)); return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    c = LlamaServerClient("http://vlm", transport=httpx.MockTransport(handler))
    c.chat([], json_schema={"type": "object"}, schema_mode="json_object")
    assert seen["response_format"] == {"type": "json_object", "schema": {"type": "object"}}

def test_image_content_base64(tmp_path):
    p = tmp_path / "a.png"; Image.new("RGB", (4, 4)).save(p)
    ic = image_content(p)
    assert ic["type"] == "image_url" and ic["image_url"]["url"].startswith("data:image/png;base64,")

def test_fake_client_records_calls():
    f = FakeVlmClient(["one", "two"])
    assert f.chat([{"role": "user", "content": "x"}]) == "one" and f.chat([]) == "two" and len(f.calls) == 2

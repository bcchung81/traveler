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

def test_chat_retries_transport_errors_and_5xx_but_not_4xx():
    seq, sleeps = [], []
    def handler(req):
        seq.append(1)
        if len(seq) == 1:
            raise httpx.ConnectError("refused", request=req)
        if len(seq) == 2:
            return httpx.Response(503, json={"error": "Loading model"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    c = LlamaServerClient("http://vlm", transport=httpx.MockTransport(handler), sleep=sleeps.append)
    assert c.chat([]) == "ok" and len(seq) == 3 and sleeps == [1, 2]
    bad = LlamaServerClient("http://vlm", transport=httpx.MockTransport(lambda r: httpx.Response(400, json={})), sleep=sleeps.append)
    try:
        bad.chat([], json_schema={"type": "object"})
        assert False
    except httpx.HTTPStatusError as e:
        assert e.response.status_code == 400  # 스키마 미지원 400은 바로 넘겨 다음 모드로 폴백하게 한다
    dead = LlamaServerClient("http://vlm", transport=httpx.MockTransport(lambda r: httpx.Response(502)), sleep=lambda s: None)
    try:
        dead.chat([])
        assert False
    except RuntimeError as e:
        assert "llama-server" in str(e)

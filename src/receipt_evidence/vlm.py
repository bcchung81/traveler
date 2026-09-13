# src/receipt_evidence/vlm.py
from __future__ import annotations
import base64, time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
import httpx

class VlmClient(Protocol):
    def chat(self, messages: list[dict], *, json_schema: dict | None = None, max_tokens: int = 2048, schema_mode: str = "json_schema") -> str: ...
    def healthy(self) -> bool: ...

def image_content(png_path: Path) -> dict:
    b64 = base64.b64encode(Path(png_path).read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}

RETRY_STATUS = frozenset({500, 502, 503, 504})  # 503: 모델 로딩 중 등 잠깐 뒤 다시 되는 응답

class LlamaServerClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8088", timeout: float = 600.0, transport: httpx.BaseTransport | None = None, model: str = "qwen3-vl",
                 attempts: int = 3, sleep: Callable[[float], None] = time.sleep):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.attempts = attempts
        self._sleep = sleep
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def healthy(self) -> bool:
        try:
            return self._http.get("/health").json().get("status") == "ok"
        except (httpx.HTTPError, ValueError):
            return False

    def chat(self, messages: list[dict], *, json_schema: dict | None = None, max_tokens: int = 2048, schema_mode: str = "json_schema") -> str:
        body: dict = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
        if json_schema is not None:
            if schema_mode == "json_object":
                body["response_format"] = {"type": "json_object", "schema": json_schema}
            else:
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "receipt", "schema": json_schema}}
        last: Exception | None = None
        for attempt in range(self.attempts):
            try:
                r = self._http.post("/v1/chat/completions", json=body)
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
            except httpx.TransportError as e:  # 연결 끊김·시간 초과
                last = e
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in RETRY_STATUS:
                    raise  # 400(스키마 미지원 등)은 호출한 쪽이 다음 모드로 폴백한다
                last = e
            if attempt < self.attempts - 1:
                self._sleep(2 ** attempt)
        raise RuntimeError(f"llama-server 응답 없음: {type(last).__name__}: {last}")

class FakeVlmClient:
    def __init__(self, replies: list[str]):
        self._replies = list(replies)
        self.calls: list[dict] = []

    def healthy(self) -> bool:
        return True

    def chat(self, messages: list[dict], *, json_schema: dict | None = None, max_tokens: int = 2048, schema_mode: str = "json_schema") -> str:
        self.calls.append({"messages": messages, "json_schema": json_schema, "schema_mode": schema_mode})
        return self._replies.pop(0)

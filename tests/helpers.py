# tests/helpers.py
"""파이프라인 테스트용: 단색 영수증 이미지를 만들고, 이미지 색으로 영수증을 식별해 답하는 가짜 VLM."""
import base64, io, json, threading
from pathlib import Path
from PIL import Image

_FIELDS = ("merchant", "business_no", "paid_at", "service_date", "service_end_date", "origin", "destination", "seat_class",
           "train_no", "card_masked", "payer_name", "region", "nights")

def color_png(path: Path, rgb: tuple[int, int, int], size=(240, 480)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, rgb).save(path, "PNG")

def spec(category: str, amount: int, approval_no: str, **kw) -> dict:
    return {k: None for k in _FIELDS} | {"category": category, "amount": amount, "approval_no": approval_no} | kw

class ColorVlm:
    def __init__(self, specs: dict[tuple[int, int, int], dict], healthy: bool = True):
        self.specs = specs
        self._healthy = healthy
        self.calls = 0
        self._lock = threading.Lock()

    def healthy(self) -> bool:
        return self._healthy

    def chat(self, messages, *, json_schema=None, max_tokens=2048, schema_mode="json_schema"):
        with self._lock:
            self.calls += 1
        url = next(c["image_url"]["url"] for c in messages[-1]["content"] if c["type"] == "image_url")
        rgb = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB").getpixel((5, 5))
        s = self.specs[tuple(rgb)]
        if json_schema is None:
            return f"결제금액 {s['amount']:,}원 승인번호 {s['approval_no']} {s.get('business_no') or ''}"
        return json.dumps(s, ensure_ascii=False)

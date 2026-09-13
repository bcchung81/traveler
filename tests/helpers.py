# tests/helpers.py
"""파이프라인 테스트용: 단색 영수증 이미지를 만들고, 이미지 색으로 영수증을 식별해 답하는 가짜 VLM."""
import base64, io, json, threading
from datetime import date
from pathlib import Path
from PIL import Image
from receipt_evidence.mcp_client import FakeToolCaller

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

TRAVELER = "grade: 제2호\nworkplace_region: 나주\napproval: [담당, 팀장]\n"

def law_from(t: dict) -> FakeToolCaller:
    """녹화한 korean-law 응답(conftest law_fixture_text)으로 답하는 가짜 법령 MCP."""
    return FakeToolCaller({"search_law": lambda a: t["search_law.txt"],
                           "get_annexes": lambda a: t["annex1.html"] if a["annexNo"] == "1" else t["annex2.html"],
                           "get_law_text": lambda a: t["jo" + a["jo"][1:-1] + ".txt"]})

def doc_fake(fail_on: str | None = None, render: bool = False) -> FakeToolCaller:
    """HWPX 대신 b"PK"를 쓰고, parse_document는 같은 폴더 report.md를 돌려주는 가짜 kordoc MCP."""
    def generate(a):
        if fail_on and fail_on in a["output_path"]:
            raise RuntimeError("kordoc 실패 재현")
        Path(a["output_path"]).write_bytes(b"PK")
        return "ok"
    handlers = {"generate_document": generate,
                "parse_document": lambda a: (Path(a["file_path"]).parent / "report.md").read_text(encoding="utf-8")}
    if render:
        handlers["render_document"] = lambda a: Path(a["output_path"]).write_text("<html><body>preview</body></html>", encoding="utf-8") and "ok"
    return FakeToolCaller(handlers)

def rail(n: int, day: date, o: str, d: str) -> dict:
    return spec("철도", 48200, f"7{n:07d}", merchant="한국철도공사", business_no="314-82-10024", service_date=day.isoformat(),
                paid_at=f"{day.isoformat()} 09:00", origin=o, destination=d, seat_class="일반실", train_no=f"KTX {100 + n}")

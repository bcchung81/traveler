# tests/test_extract.py
import json, threading
from datetime import date, datetime
from pathlib import Path
import httpx
from PIL import Image
from receipt_evidence.cache import ExtractCache
from receipt_evidence.models import ReceiptImage, Category
from receipt_evidence.vlm import FakeVlmClient
from receipt_evidence.extract import extract_receipts, parse_json_loose, parse_date, parse_datetime, mask_card, to_receipt

KTX = {"category": "철도", "merchant": "한국철도공사", "business_no": "314-82-10024", "amount": 48200, "paid_at": "2026.07.08 22:10",
       "service_date": "2026-07-09(목)", "service_end_date": None, "origin": "나주", "destination": "용산", "seat_class": "일반실",
       "train_no": "KTX-산천 424", "approval_no": "55431218", "card_masked": "53618190****574*", "payer_name": None, "region": None, "nights": None}

def _img(tmp_path):
    p = tmp_path / "abc-p1.png"; Image.new("RGB", (8, 8)).save(p)
    return ReceiptImage(image_id="abc-p1", source_path="x.jpg", png_path=str(p), sha256="abc", width=8, height=8)

def test_parse_helpers():
    assert parse_json_loose("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert parse_date("2026-07-09(목)") == date(2026, 7, 9) and parse_date("2026/08/21 16:44:06") == date(2026, 8, 21)
    assert parse_datetime("2026.07.08 22:10") == datetime(2026, 7, 8, 22, 10) and parse_date("없음") is None
    assert parse_date("2026년 07월 09일 (목)") == date(2026, 7, 9)  # 코레일 영수증 원문 표기
    assert parse_datetime("2026년 06월 11일 (목) 08:31") == datetime(2026, 6, 11, 8, 31)
    assert mask_card("5361819012345741") == "536181******41" and mask_card("53618190****574*") == "53618190****574*"

def test_two_pass_and_schema(tmp_path):
    vlm = FakeVlmClient(["전사문: 결제금액 48,200원", json.dumps(KTX, ensure_ascii=False)])
    rs = extract_receipts(vlm, [_img(tmp_path)], tmp_path / "out")
    r = rs[0]
    assert r.category is Category.RAIL and r.amount == 48200 and r.service_date == date(2026, 7, 9) and r.approval_no == "55431218"
    assert r.sha256 == "abc"
    assert vlm.calls[0]["json_schema"] is None and vlm.calls[1]["json_schema"]["type"] == "object"
    assert (tmp_path / "out" / "transcripts" / "abc-p1.txt").read_text(encoding="utf-8").startswith("전사문")
    assert any(c["type"] == "image_url" for c in vlm.calls[1]["messages"][-1]["content"])

def test_fallback_to_json_object_then_loose(tmp_path):
    vlm = FakeVlmClient(["t", "not json", "still not", "```json\n" + json.dumps(KTX) + "\n```"])
    r = extract_receipts(vlm, [_img(tmp_path)], tmp_path / "out")[0]
    assert r.amount == 48200 and vlm.calls[2]["schema_mode"] == "json_object"

def test_extract_failed_marks_unknown(tmp_path):
    vlm = FakeVlmClient(["t", "x", "y", "z"])
    r = extract_receipts(vlm, [_img(tmp_path)], tmp_path / "out")[0]
    assert r.category is Category.UNKNOWN and "EXTRACT_FAILED" in r.warnings

def test_to_receipt_unknown_category():
    r = to_receipt("i", {"category": "이상한값", "amount": "1,000"}, "t.txt")
    assert r.category is Category.UNKNOWN and r.amount == 1000

def test_multipage_pdf_becomes_one_receipt(tmp_path):
    imgs = []
    for page in (1, 2):
        p = tmp_path / f"pdf-p{page}.png"; Image.new("RGB", (8, 8)).save(p)
        imgs.append(ReceiptImage(image_id=f"pdf-p{page}", source_path="stay.pdf", page=page, png_path=str(p), sha256="pdf", width=8, height=8))
    stay = dict(KTX, category="숙박", amount=100000, approval_no="68325420")
    vlm = FakeVlmClient(["결제금액 100,000원", "토스페이먼츠 주식회사", json.dumps(stay, ensure_ascii=False)])
    rs = extract_receipts(vlm, imgs, tmp_path / "out")
    assert len(rs) == 1 and rs[0].receipt_id == "pdf-p1" and rs[0].amount == 100000
    assert sum(c["type"] == "image_url" for c in vlm.calls[2]["messages"][-1]["content"]) == 2
    assert "[2쪽]" in (tmp_path / "out" / "transcripts" / "pdf-p1.txt").read_text(encoding="utf-8")

class _Http400OnJsonSchema(FakeVlmClient):
    """llama-server가 json_schema response_format을 400으로 거부하는 상황 재현"""
    def chat(self, messages, *, json_schema=None, max_tokens=2048, schema_mode="json_schema"):
        if json_schema is not None and schema_mode == "json_schema":
            req = httpx.Request("POST", "http://vlm/v1/chat/completions")
            raise httpx.HTTPStatusError("400", request=req, response=httpx.Response(400, request=req))
        return super().chat(messages, json_schema=json_schema, max_tokens=max_tokens, schema_mode=schema_mode)

def test_http_400_on_json_schema_falls_back_to_json_object(tmp_path):
    vlm = _Http400OnJsonSchema(["t", json.dumps(KTX, ensure_ascii=False)])
    r = extract_receipts(vlm, [_img(tmp_path)], tmp_path / "out")[0]
    assert r.amount == 48200 and vlm.calls[-1]["schema_mode"] == "json_object"

def test_cache_skips_vlm_on_second_run(tmp_path):
    cache = ExtractCache(tmp_path / ".cache")
    vlm = FakeVlmClient(["결제금액 48,200원", json.dumps(KTX, ensure_ascii=False)])
    r1 = extract_receipts(vlm, [_img(tmp_path)], tmp_path / "out1", cache=cache)[0]
    vlm2 = FakeVlmClient([])
    r2 = extract_receipts(vlm2, [_img(tmp_path)], tmp_path / "out2", cache=cache)[0]
    assert r1.amount == r2.amount == 48200 and vlm2.calls == [] and (cache.hits, cache.misses) == (1, 1)
    assert r2.sha256 == "abc" and (tmp_path / "out2" / "transcripts" / "abc-p1.txt").read_text(encoding="utf-8") == "결제금액 48,200원"
    assert (cache.dir / "abc.json").exists() and cache.dir.parent.name == "extract"

def test_failed_extraction_is_not_cached(tmp_path):
    cache = ExtractCache(tmp_path / ".cache")
    extract_receipts(FakeVlmClient(["t", "x", "y", "z"]), [_img(tmp_path)], tmp_path / "out", cache=cache)
    assert not cache.has("abc")

class _ConstantVlm:
    def __init__(self):
        self.calls = 0; self._lock = threading.Lock()
    def healthy(self):
        return True
    def chat(self, messages, *, json_schema=None, max_tokens=2048, schema_mode="json_schema"):
        with self._lock:
            self.calls += 1
        return json.dumps(KTX, ensure_ascii=False) if json_schema else "결제금액 48,200원"

def test_workers_preserve_order(tmp_path):
    imgs = []
    for i in range(6):
        p = tmp_path / f"i{i}-p1.png"; Image.new("RGB", (8, 8)).save(p)
        imgs.append(ReceiptImage(image_id=f"i{i}-p1", source_path=f"{i}.jpg", png_path=str(p), sha256=f"s{i}", width=8, height=8))
    vlm = _ConstantVlm()
    rs = extract_receipts(vlm, imgs, tmp_path / "out", workers=4)
    assert [r.receipt_id for r in rs] == [f"i{i}-p1" for i in range(6)] and vlm.calls == 12

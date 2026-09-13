# tests/integration/test_e2e.py
import json, shutil, unicodedata
from pathlib import Path
import pytest
from receipt_evidence.mcp_client import kordoc_caller, law_caller
from receipt_evidence.models import Category, Verdict
from receipt_evidence.pipeline import Clients, run_batch
from receipt_evidence.vlm import LlamaServerClient

ROOT = Path(__file__).resolve().parents[2]
GOLD = {g["approval_no"]: g for g in json.loads((ROOT / "tests/fixtures/golden/receipts.json").read_text(encoding="utf-8"))}
SRC = {unicodedata.normalize("NFC", p.name): p for p in (ROOT / "data").rglob("*") if p.is_file()}  # 사용자가 data/를 폴더로 정리한 뒤에도 원본을 찾는다

class CountingVlm:
    def __init__(self, inner):
        self.inner, self.calls = inner, 0
    def healthy(self):
        return self.inner.healthy()
    def chat(self, messages, **kw):
        self.calls += 1
        return self.inner.chat(messages, **kw)

@pytest.mark.integration
def test_e2e_real_receipts_incremental(tmp_path):
    vlm = CountingVlm(LlamaServerClient())
    if not vlm.healthy():
        pytest.skip("llama-server 미기동 — bash scripts/start_vlm.sh & 후 재실행")
    data, out = tmp_path / "data", tmp_path / "out"
    trav = data / "정백철"; trip_dir = trav / "2026-07-09_서울"; trip_dir.mkdir(parents=True)
    (trav / "traveler.yaml").write_text("grade: 제2호\nworkplace_region: 나주\napproval: [담당, 팀장, 부장]\n", encoding="utf-8")
    (trip_dir / "trip.yaml").write_text("start_date: 2026-07-09\nend_date: 2026-07-10\ndestination_region: 서울\nroute_stations: [나주, 용산]\n", encoding="utf-8")
    for name in ("Screenshot_20260713_083024.jpg", "Screenshot_20260713_083037.jpg"):
        shutil.copy(SRC[name], trip_dir / name)

    with law_caller() as law, kordoc_caller() as doc:
        clients = Clients(vlm=vlm, law=law, doc=doc)

        r1 = run_batch(data, out, clients, run_id="e1").results[0]
        assert r1.error is None and r1.version == 1 and r1.verify_ok and len(r1.receipts) == 2 and r1.law_mst == "287535"
        for r in r1.receipts:
            g = GOLD[r.approval_no]
            assert r.category is Category(g["category"]) and r.amount == g["amount"] and str(r.service_date) == g["service_date"]
        assert r1.totals == {"claimed": 96400, "approved": 196400, "review": 0}

        shutil.copy(SRC["숙박 영수증.pdf"], trip_dir / "숙박 영수증.pdf")
        before = vlm.calls
        r2 = run_batch(data, out, clients, run_id="e2").results[0]
        assert r2.version == 2 and (r2.cache_hits, r2.cache_misses) == (2, 1) and vlm.calls - before >= 3
        assert r2.totals == {"claimed": 196400, "approved": 196400, "review": 100000} and r2.verify_ok
        stay = next(r for r in r2.receipts if r.category is Category.LODGING)
        assert next(d for d in r2.decisions if d.receipt_id == stay.receipt_id).verdict is Verdict.REVIEW
        assert "숙박비" in Path(r2.changes_md_path).read_text(encoding="utf-8")
        md = Path(r2.report_md_path).read_text(encoding="utf-8")
        assert "kca.kr" not in md and "53618190" not in md

        (trip_dir / "overrides.yaml").write_text(f"{stay.receipt_id}:\n  service_date: 2026-07-09\n  region: 서울\n", encoding="utf-8")
        before = vlm.calls
        r3 = run_batch(data, out, clients, run_id="e3").results[0]
        assert r3.version == 3 and vlm.calls == before and r3.totals["approved"] == 296400 and r3.verify_ok

        r4 = run_batch(data, out, clients, run_id="e4").results[0]
        assert r4.skipped and r4.version == 3

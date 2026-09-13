# tests/test_pipeline_hooks.py
import json
from datetime import date
import pytest
from helpers import TRAVELER, ColorVlm, color_png, doc_fake, law_from, rail
from receipt_evidence.law import get_law_snapshot
from receipt_evidence.pipeline import Clients, extract_trip, find_job, review_receipts, run_batch

def _setup(tmp_path, t):
    data, out = tmp_path / "data", tmp_path / "out"
    trav = data / "정백철"; trip_dir = trav / "2026-07-09_서울"; trip_dir.mkdir(parents=True)
    (trav / "traveler.yaml").write_text(TRAVELER, encoding="utf-8")
    (trip_dir / "trip.yaml").write_text("start_date: 2026-07-09\nend_date: 2026-07-10\ndestination_region: 서울\n", encoding="utf-8")
    color_png(trip_dir / "k1.png", (10, 20, 30))
    clients = Clients(vlm=ColorVlm({(10, 20, 30): rail(1, date(2026, 7, 9), "나주", "용산")}), law=law_from(t), doc=doc_fake())
    return data, out, trip_dir, clients

def test_extract_trip_writes_extracted_and_applies_overrides(tmp_path, law_fixture_text):
    data, out, trip_dir, clients = _setup(tmp_path, law_fixture_text)
    work = extract_trip(data, out, clients, "정백철", "2026-07-09_서울")
    rid = work.receipts[0].receipt_id
    extracted = out / "정백철" / "2026-07-09_서울" / "work" / "receipts.extracted.json"
    assert [r["receipt_id"] for r in json.loads(extracted.read_text(encoding="utf-8"))] == [rid]
    (trip_dir / "overrides.yaml").write_text(f"{rid}:\n  amount: 50000\n", encoding="utf-8")
    work2 = extract_trip(data, out, clients, "정백철", "2026-07-09_서울")
    assert work2.receipts[0].amount == 50000 and work2.cache_hits == 1
    assert json.loads(extracted.read_text(encoding="utf-8"))[0]["amount"] == 48200

def test_extract_trip_unknown_trip_raises(tmp_path, law_fixture_text):
    data, out, _, clients = _setup(tmp_path, law_fixture_text)
    with pytest.raises(LookupError):
        extract_trip(data, out, clients, "정백철", "2026-01-01_없음")

def test_review_receipts_matches_finalize_without_summary(tmp_path, law_fixture_text):
    data, out, _, clients = _setup(tmp_path, law_fixture_text)
    work = extract_trip(data, out, clients, "정백철", "2026-07-09_서울")
    law = get_law_snapshot(clients.law, out / ".cache", date.today())
    review = review_receipts(find_job(data, "정백철", "2026-07-09_서울"), work.receipts, law)
    assert review.totals == {"claimed": 48200, "approved": 148200, "review": 0} and not review.trip.proposed
    res = run_batch(data, out, clients, run_id="w1", summary=False).results[0]
    assert res.totals == review.totals and not list(out.glob("summary-*"))

def test_on_vlm_needed_called_only_on_cache_miss_when_down(tmp_path, law_fixture_text):
    data, out, trip_dir, clients = _setup(tmp_path, law_fixture_text)
    clients.vlm._healthy = False
    calls = []
    def bring_up():
        calls.append(1); clients.vlm._healthy = True
    extract_trip(data, out, clients, "정백철", "2026-07-09_서울", on_vlm_needed=bring_up)
    assert calls == [1]
    clients.vlm._healthy = False
    extract_trip(data, out, clients, "정백철", "2026-07-09_서울", on_vlm_needed=bring_up)  # 전부 캐시 → 훅 호출 안 함
    run_batch(data, out, clients, run_id="w2", summary=False, on_vlm_needed=bring_up)
    assert calls == [1]
    color_png(trip_dir / "k2.png", (40, 50, 60))
    clients.vlm.specs[(40, 50, 60)] = rail(2, date(2026, 7, 10), "용산", "나주")
    clients.vlm._healthy = False
    with pytest.raises(RuntimeError, match="llama-server"):
        extract_trip(data, out, clients, "정백철", "2026-07-09_서울", on_vlm_needed=lambda: None)  # 훅 뒤에도 무응답

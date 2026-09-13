# tests/web/test_review_screen.py
from urllib.parse import unquote
from helpers import new_trip

BASE = "/t/정백철/2026-07-09_서울"
FILES = (("k1.png", (10, 20, 30)), ("k2.png", (40, 50, 60)), ("stay.png", (70, 80, 90)))

def test_review_decisions_and_resolve(web):
    new_trip(web, files=FILES)
    web.post(f"{BASE}/extract")
    page = web.get(f"{BASE}/review")
    assert page.status_code == 200 and "196,400" in page.text and "잠깐, 확인!" in page.text and "출장기간" in page.text
    assert "HWPX 증빙서류 만들기" in page.text
    stay = next(x for x in web.deps.service.receipts("정백철", "2026-07-09_서울") if x.amount == 100000)
    r = web.post(f"{BASE}/receipts/{stay.receipt_id}", data={"service_date": "2026-07-09", "region": "서울", "next": "review"})
    assert unquote(r.headers["location"]) == f"{BASE}/review"
    page = web.get(f"{BASE}/review")
    assert "296,400" in page.text and "잠깐, 확인!" not in page.text
    r = web.post(f"{BASE}/resolve", data={"lodging_region": "서울", "taxi_reason": ""})
    assert r.status_code == 303 and web.deps.service.load_trip_yaml("정백철", "2026-07-09_서울")["lodging_region"] == "서울"

def test_review_warms_law_when_cache_missing(web):
    new_trip(web)
    web.post(f"{BASE}/extract")
    for f in (web.deps.service.out_dir / ".cache" / "law").glob("*.json"):
        f.unlink()
    page = web.get(f"{BASE}/review")
    assert page.status_code == 200 and "148,200" in page.text

def test_review_redirects_before_extraction(web):
    new_trip(web)
    r = web.get(f"{BASE}/review")
    assert r.status_code == 303 and unquote(r.headers["location"]) == f"{BASE}/upload"

# tests/web/test_review_screen.py
from urllib.parse import unquote
from helpers import new_trip, png_bytes, upload_new

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
    from datetime import date
    s = web.deps.service
    s.create_trip("정백철", date(2026, 7, 9), "서울")
    s.save_files("정백철", "2026-07-09_서울", [("k1.png", png_bytes((10, 20, 30)))])
    r = web.get(f"{BASE}/review")
    assert r.status_code == 303 and unquote(r.headers["location"]) == f"{BASE}/upload"

from contextlib import contextmanager
from helpers import doc_fake, png_bytes
from receipt_evidence.mcp_client import FakeToolCaller
from receipt_evidence.pipeline import Clients

class _Offline(FakeToolCaller):
    def call_many(self, calls):
        raise RuntimeError("MCP 서버 시작 실패(npx): ENOTFOUND")

def _go_offline(web):
    vlm = web.vlm_fake
    @contextmanager
    def clients():
        yield Clients(vlm=vlm, law=_Offline({}), doc=doc_fake(render=True))
    web.deps.clients = clients

def test_offline_extract_and_review_use_stored_law(web):
    _go_offline(web)
    new_trip(web)
    web.post(f"{BASE}/extract")
    page = web.get(f"{BASE}/extract")
    assert "문제가 생겼어요" not in page.text and "48,200" in page.text  # 규정 조회 실패가 읽기 실패로 보이지 않는다
    page = web.get(f"{BASE}/review")
    assert page.status_code == 200 and "148,200" in page.text and "인터넷에 연결되지 않아" in page.text
    r = web.post(f"{BASE}/finalize")
    assert "서류가 완성됐어요" in web.get(unquote(r.headers["location"])).text

def test_stale_files_block_document_creation(web):
    new_trip(web)
    web.post(f"{BASE}/extract")
    (web.deps.service.trip_dir("정백철", "2026-07-09_서울") / "k2.png").write_bytes(png_bytes((40, 50, 60)))  # 탐색기로 넣은 파일
    page = web.get(f"{BASE}/review")
    assert "파일이 바뀌었어요" in page.text and "HWPX 증빙서류 만들기" not in page.text
    r = web.post(f"{BASE}/finalize")
    assert unquote(r.headers["location"]) == f"{BASE}/review?error=stale" and not web.deps.service.versions("정백철", "2026-07-09_서울")
    assert "다시 읽은 뒤에" in web.get(unquote(r.headers["location"])).text

def test_review_flags_same_file_in_another_trip(web):
    new_trip(web)
    r = upload_new(web, files=(("again.png", (10, 20, 30)),))
    assert unquote(r.headers["location"]) == f"{BASE}_2/extract"  # 같은 날짜·출장지 폴더가 있으면 _2
    page = web.get(f"{BASE}/review")
    assert "다른 출장" in page.text and "2026-07-09_서울_2" in page.text

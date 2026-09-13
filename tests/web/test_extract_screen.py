# tests/web/test_extract_screen.py
from urllib.parse import unquote
from helpers import new_trip

BASE = "/t/정백철/2026-07-09_서울"

def test_extract_flow_edit_and_image(web):
    new_trip(web, files=(("k1.png", (10, 20, 30)), ("stay.png", (70, 80, 90))))
    r = web.post(f"{BASE}/extract")
    assert r.status_code == 303 and unquote(r.headers["location"]) == f"{BASE}/extract"
    page = web.get(f"{BASE}/extract")
    rs = web.deps.service.receipts("정백철", "2026-07-09_서울")
    assert page.status_code == 200 and len(rs) == 2 and "AI가 읽은 값" in page.text and "48,200" in page.text
    assert "숙박 지역" not in page.text and "출발" in page.text  # 철도 영수증에는 숙박 칸을 보여 주지 않는다
    stay = next(x for x in rs if x.amount == 100000)
    page = web.get(f"{BASE}/extract", params={"rid": stay.receipt_id})
    assert f"/image/{stay.receipt_id}" in unquote(page.text) and "(주)예시숙박" in page.text and "MISSING_BIZNO" in page.text
    assert "숙박 지역" in page.text and "체크아웃" in page.text and "열차·편명" not in page.text
    img = web.get(f"{BASE}/image/{stay.receipt_id}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert web.get(f"{BASE}/image/..%2Fx").status_code in (400, 404)
    r = web.post(f"{BASE}/receipts/{stay.receipt_id}", data={"service_date": "2026-07-09", "region": "서울", "clear_warnings": ["MISSING_BIZNO"]})
    assert r.status_code == 303 and "rid=" in r.headers["location"]
    fixed = next(x for x in web.deps.service.receipts("정백철", "2026-07-09_서울") if x.receipt_id == stay.receipt_id)
    assert fixed.region == "서울" and "MISSING_BIZNO" not in fixed.warnings
    assert web.get(f"{BASE}/job").headers.get("HX-Refresh") == "true"

def test_extract_error_panel_when_vlm_down(web):
    new_trip(web)
    web.vlm_fake._healthy = False
    web.deps.vlm.health = lambda: False
    web.deps.vlm.popen = lambda *a, **k: None   # 켜기 실패 재현(프로세스 없음)
    web.post(f"{BASE}/extract")
    page = web.get(f"{BASE}/extract")
    assert page.status_code == 200 and "llama-server" in page.text and "다시 읽기" in page.text

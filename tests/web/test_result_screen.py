# tests/web/test_result_screen.py
from urllib.parse import unquote
from helpers import new_trip

BASE = "/t/정백철/2026-07-09_서울"

def test_finalize_result_preview_download_versions_and_home(web):
    new_trip(web)
    web.post(f"{BASE}/extract")
    r = web.post(f"{BASE}/finalize")
    assert r.status_code == 303 and unquote(r.headers["location"]) == f"{BASE}/result"
    page = web.get(f"{BASE}/result")
    text = unquote(page.text)
    assert page.status_code == 200 and "서류가 완성됐어요" in text and "148,200" in text and "v1" in text
    assert f"{BASE}/v/1/preview" in text and "합계를 다시 읽어 맞춰 봤어요" in text and f"{BASE}/v/1/evidence.hwpx" in text
    d = web.get(f"{BASE}/v/1/evidence.hwpx")
    assert d.status_code == 200 and d.content == b"PK" and "attachment" in d.headers["content-disposition"]
    p = web.get(f"{BASE}/v/1/preview")
    assert p.status_code == 200 and "default-src 'none'" in p.headers["content-security-policy"]
    assert web.get(f"{BASE}/v/9/evidence.hwpx").status_code == 404
    web.post(f"{BASE}/info", data={"purpose": "변경"})
    web.post(f"{BASE}/finalize")
    text = unquote(web.get(f"{BASE}/result").text)
    assert "v2" in text and web.get(f"{BASE}/v/2/changes").status_code == 200
    home = unquote(web.get("/").text)
    assert "2026-07-09_서울" in home and "v2" in home and "148,200" in home

def test_result_before_any_version_redirects_to_review(web):
    new_trip(web)
    web.post(f"{BASE}/extract")
    r = web.get(f"{BASE}/result")
    assert r.status_code == 303 and unquote(r.headers["location"]) == f"{BASE}/review"

# tests/web/test_upload.py
from datetime import date
from urllib.parse import unquote
from helpers import new_trip, png_bytes

BASE = "/t/정백철/2026-07-09_서울"

def test_new_trip_creates_folder_yaml_and_files(web):
    assert web.get("/new").status_code == 200
    r = new_trip(web)
    assert r.status_code == 303 and unquote(r.headers["location"]) == f"{BASE}/upload"
    s = web.deps.service
    assert [f.name for f in s.list_files("정백철", "2026-07-09_서울")] == ["k1.png"]
    assert s.load_profile("정백철").grade == "제2호" and s.load_trip_yaml("정백철", "2026-07-09_서울")["route_stations"] == ["나주", "용산"]
    page = web.get(f"{BASE}/upload")
    assert page.status_code == 200 and "k1.png" in page.text and 'value="회의"' in page.text and "step--current" in page.text
    assert "영수증 읽기 시작" in page.text

def test_upload_add_delete_info_and_errors(web):
    new_trip(web)
    assert web.post(f"{BASE}/files", files=[("files", ("k2.png", png_bytes((40, 50, 60)), "image/png"))]).status_code == 303
    assert "k2.png" in web.get(f"{BASE}/upload").text
    assert web.post(f"{BASE}/files", files=[("files", ("memo.txt", b"x", "text/plain"))]).status_code == 400
    assert web.post(f"{BASE}/files/delete", data={"name": "k2.png"}).status_code == 303
    assert "k2.png" not in web.get(f"{BASE}/upload").text
    r = web.post(f"{BASE}/info", data={"grade": "제1호", "end_date": "2026-07-11"})
    assert r.status_code == 303
    assert web.deps.service.load_profile("정백철").grade == "제1호"
    assert web.deps.service.load_trip_yaml("정백철", "2026-07-09_서울")["end_date"] == date(2026, 7, 11)
    assert web.deps.service.load_trip_yaml("정백철", "2026-07-09_서울")["purpose"] == "회의"

def test_bad_path_names_rejected(web):
    assert web.get("/t/.hidden/2026-07-09_서울/upload").status_code == 400
    assert web.post("/t/정백철/..%2Fx/files/delete", data={"name": "a.png"}).status_code in (400, 404)

def test_extract_without_files_shows_message(web):
    web.deps.service.create_trip("정백철", date(2026, 7, 9), "서울")
    r = web.post(f"{BASE}/extract")
    assert r.status_code == 303 and "영수증을 먼저 올려 주세요" in web.get(unquote(r.headers["location"])).text

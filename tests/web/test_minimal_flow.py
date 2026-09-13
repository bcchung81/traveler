# tests/web/test_minimal_flow.py — 첫 화면 최소 입력·영수증 자동 채움·필요할 때만 묻기
from datetime import date
from urllib.parse import quote, unquote
from helpers import DOC_INFO, TRIP_CONFIRM, png_bytes, upload_new

def test_first_screen_asks_only_for_receipts_and_traveler(web):
    page = web.get("/new").text
    assert "영수증을 여기에 끌어다 놓으세요" in page and 'name="traveler_new"' in page and "steps-line" in page
    for name in ("start_date", "destination_region", "purpose", "approval", "grade", "workplace_region", "route_stations", "org"):
        assert f'name="{name}"' not in page, name
    web.deps.service.create_trip("정백철", date(2026, 7, 9), "서울")
    page = web.get("/new").text
    assert 'type="radio" name="traveler" value="정백철" checked' in page  # 출장자가 한 명이면 골라 둔다

def test_upload_reads_renames_and_defaults_grade(web):
    r = upload_new(web, traveler="홍길동")
    assert r.status_code == 303 and unquote(r.headers["location"]) == "/t/홍길동/2026-07-09_서울/extract"
    s = web.deps.service
    assert s.load_profile("홍길동").grade == "제2호"  # 새 출장자는 제2호 기본
    assert [p.name for p in (s.data_dir / "홍길동").iterdir() if p.is_dir()] == ["2026-07-09_서울"]
    assert web.vlm_fake.calls == 2 and len(s.receipts("홍길동", "2026-07-09_서울")) == 1

def test_old_staging_address_redirects(web):
    r = upload_new(web)
    staging = next(k.split("/", 1)[1] for k in __import__("json").loads((web.deps.service.out_dir / ".cache" / "renames.json").read_text()))
    old = f"/t/정백철/{quote(staging, safe='')}"
    g = web.get(f"{old}/extract?rid=x")
    assert g.status_code == 303 and unquote(g.headers["location"]) == "/t/정백철/2026-07-09_서울/extract?rid=x"
    h = web.get(f"{old}/job", headers={"HX-Request": "true", "HX-Current-URL": f"http://testserver{quote(old)}/extract"})
    assert unquote(h.headers["HX-Redirect"]) == "/t/정백철/2026-07-09_서울/extract"

def test_trip_card_autofills_from_receipts_and_confirms_in_one_click(web):
    upload_new(web, files=(("k1.png", (10, 20, 30)), ("k2.png", (40, 50, 60))))
    base = "/t/정백철/2026-07-09_서울"
    page = web.get(f"{base}/extract").text
    assert 'id="trip-form"' in page and 'value="2026-07-09"' in page and 'value="2026-07-10"' in page and 'value="서울"' in page
    assert "KTX 101 나주→용산 7/9" in page and 'value="나주"' in page and "자동" in page
    assert 'form="trip-form"' in page and "맞아요, 규정 확인하기" in page  # 머리 버튼 한 번으로 확정
    review = web.get(f"{base}/review").text
    assert "자동 제안값" in review  # 확정 전에는 일비·식비가 확인필요
    r = web.post(f"{base}/trip", data={"start_date": "2026-07-09", "end_date": "2026-07-10", "destination_region": "서울",
                                       "workplace_region": "나주", "grade": "제2호", "route_stations": "나주, 용산"})
    assert unquote(r.headers["location"]) == f"{base}/review"
    s = web.deps.service
    assert s.load_trip_yaml("정백철", "2026-07-09_서울")["end_date"] == date(2026, 7, 10) and s.load_profile("정백철").workplace_region == "나주"
    review = web.get(f"{base}/review").text
    assert "196,400" in review and "자동 제안값" not in review
    page = web.get(f"{base}/extract").text
    assert "<details" in page and "2026. 7. 9." in page  # 확정 후에는 한 줄 요약으로 접는다

def test_confirm_renames_folder_when_user_corrects_destination(web):
    upload_new(web)
    r = web.post("/t/정백철/2026-07-09_서울/trip", data=TRIP_CONFIRM | {"destination_region": "부산"})
    assert unquote(r.headers["location"]) == "/t/정백철/2026-07-09_부산/review"
    assert web.get("/t/정백철/2026-07-09_서울/review").status_code == 303

def test_lodging_only_trip_asks_vehicle_and_renames_on_confirm(web):
    r = upload_new(web, files=(("stay.png", (70, 80, 90)),))
    location = unquote(r.headers["location"])
    assert "/_새정산-" in location  # 숙박 영수증만으로는 출장지를 몰라 이름을 바꾸지 않는다
    page = web.get(location).text
    assert 'value="2026-08-21"' in page
    r = web.post(location.replace("/extract", "/trip"), data={"start_date": "2026-08-21", "end_date": "2026-08-22", "destination_region": "서울",
                                                              "lodging_region": "서울", "grade": "제2호", "workplace_region": "나주"})
    base = "/t/정백철/2026-08-21_서울"
    assert unquote(r.headers["location"]) == f"{base}/review"
    review = web.get(f"{base}/review").text
    assert "공용차량을 이용했나요?" in review and "50,000" in review
    web.post(f"{base}/resolve", data={"official_vehicle": "on"})
    review = web.get(f"{base}/review").text
    assert "공용차량을 이용했나요?" not in review and "25,000" in review  # 일비 25,000×2일의 2분의 1

def test_same_city_asks_in_city_trip(web):
    upload_new(web)
    base = "/t/정백철/2026-07-09_서울"
    r = web.post(f"{base}/trip", data=TRIP_CONFIRM | {"destination_region": "나주", "workplace_region": "나주시"})
    base = unquote(r.headers["location"]).rsplit("/", 1)[0]
    review = web.get(f"{base}/review").text
    assert "근무지 내 출장인가요?" in review
    web.post(f"{base}/resolve", data={"within_workplace": "on", "duration_hours": "5"})
    review = web.get(f"{base}/review").text
    assert "근무지 내 출장인가요?" not in review and "근무지 내 출장 여비" in review and "20,000" in review

def test_doc_info_panel_saves_and_makes_document(web):
    upload_new(web)
    base = "/t/정백철/2026-07-09_서울"
    web.post(f"{base}/trip", data=TRIP_CONFIRM)
    review = web.get(f"{base}/review").text
    assert "서류에 들어갈 정보" in review and "출장 목적이 비어 있어요" in review and 'form="docinfo"' in review
    r = web.post(f"{base}/docinfo", data=DOC_INFO | {"org": "국립전파연구원", "next": "finalize"})
    assert unquote(r.headers["location"]) == f"{base}/result"
    s = web.deps.service
    assert s.load_trip_yaml("정백철", "2026-07-09_서울")["purpose"] == "회의" and s.load_profile("정백철").org == "국립전파연구원"
    assert [v.version for v in s.versions("정백철", "2026-07-09_서울")] == [1]
    assert "출장 목적이 비어 있어요" not in web.get(f"{base}/review").text

def test_home_labels_unconfirmed_trip(web):
    upload_new(web)
    home = unquote(web.get("/").text)
    assert "출장 정보 확인" in home and "/t/정백철/2026-07-09_서울/extract" in home

# tests/web/test_service.py
import unicodedata
from datetime import date
import pytest, yaml
from helpers import TRAVELER, ColorVlm, color_png, doc_fake, law_from, rail
from receipt_evidence.pipeline import Clients, extract_trip, run_batch
from receipt_evidence.web.service import MAX_UPLOAD_BYTES, InvalidName, TripService
from receipt_evidence.workspace import NotFound

def _svc(tmp_path):
    return TripService(tmp_path / "data", tmp_path / "out")

def _extracted(tmp_path, t):
    s = _svc(tmp_path)
    traveler, trip_id = s.create_trip("정백철", date(2026, 7, 9), "서울")
    (s.data_dir / traveler / "traveler.yaml").write_text(TRAVELER, encoding="utf-8")
    color_png(s.trip_dir(traveler, trip_id) / "k1.png", (10, 20, 30))
    clients = Clients(vlm=ColorVlm({(10, 20, 30): rail(1, date(2026, 7, 9), "나주", "용산")}), law=law_from(t), doc=doc_fake())
    extract_trip(s.data_dir, s.out_dir, clients, traveler, trip_id)
    return s, traveler, trip_id, clients

def test_create_trip_and_save_files(tmp_path):
    s = _svc(tmp_path)
    traveler, trip_id = s.create_trip(unicodedata.normalize("NFD", "정백철"), date(2026, 7, 9), "서울")
    assert (traveler, trip_id) == ("정백철", "2026-07-09_서울") and s.trip_dir(traveler, trip_id).is_dir()
    saved = s.save_files(traveler, trip_id, [("../../k1.png", b"a"), ("k1.png", b"b"), ("k1.png", b"a"), ("영수증.PDF", b"c")])
    assert saved == ["k1.png", "k1-2.png", "k1.png", "영수증.PDF"]
    assert [f.name for f in s.list_files(traveler, trip_id)] == ["k1-2.png", "k1.png", "영수증.PDF"]
    assert [f.kind for f in s.list_files(traveler, trip_id)] == ["사진", "사진", "PDF"]
    s.delete_file(traveler, trip_id, "k1-2.png")
    assert [f.name for f in s.list_files(traveler, trip_id)] == ["k1.png", "영수증.PDF"]

def test_rejects_bad_names_types_and_sizes(tmp_path):
    s = _svc(tmp_path); s.create_trip("정백철", date(2026, 7, 9), "서울")
    for bad in ("..", ".hidden", "a/b", "a\\b", "", "x" * 61):
        with pytest.raises(InvalidName):
            s.trip_dir(bad, "2026-07-09_서울")
    with pytest.raises(ValueError, match="확장자"):
        s.save_files("정백철", "2026-07-09_서울", [("memo.txt", b"x")])
    with pytest.raises(ValueError, match="30MB"):
        s.save_files("정백철", "2026-07-09_서울", [("big.jpg", b"x" * (MAX_UPLOAD_BYTES + 1))])
    with pytest.raises(InvalidName):
        s.delete_file("정백철", "2026-07-09_서울", "../traveler.yaml")

def test_profile_and_trip_yaml_roundtrip(tmp_path):
    s = _svc(tmp_path); traveler, trip_id = s.create_trip("정백철", date(2026, 7, 9), "서울")
    s.save_profile(traveler, {"grade": "제2호", "workplace_region": "나주", "approval": "담당, 팀장 ,부장", "org": ""})
    assert yaml.safe_load((s.data_dir / traveler / "traveler.yaml").read_text(encoding="utf-8")) == {"grade": "제2호", "workplace_region": "나주", "approval": ["담당", "팀장", "부장"]}
    assert s.load_profile(traveler).approval == ["담당", "팀장", "부장"]
    s.save_trip_yaml(traveler, trip_id, {"start_date": "2026-07-09", "end_date": "2026-07-10", "destination_region": "서울", "purpose": "", "unknown": "x", "route_stations": "나주, 용산"})
    s.save_trip_yaml(traveler, trip_id, {"lodging_region": "서울", "official_vehicle": "on"})
    y = s.load_trip_yaml(traveler, trip_id)
    assert y["start_date"] == date(2026, 7, 9) and y["lodging_region"] == "서울" and y["purpose"] is None and "unknown" not in y
    assert y["route_stations"] == ["나주", "용산"] and y["official_vehicle"] is True

def test_status_stages_and_staleness(tmp_path, law_fixture_text):
    s = _svc(tmp_path); traveler, trip_id = s.create_trip("정백철", date(2026, 7, 9), "서울")
    assert s.trip_status(traveler, trip_id).stage == "empty"
    s2, traveler, trip_id, clients = _extracted(tmp_path / "b", law_fixture_text)
    st = s2.trip_status(traveler, trip_id)
    assert (st.stage, st.files, st.extracted, st.stale, st.proposed) == ("extracted", 1, True, False, True)
    color_png(s2.trip_dir(traveler, trip_id) / "k2.png", (40, 50, 60))
    st = s2.trip_status(traveler, trip_id)
    assert st.stale and st.stage == "uploaded" and [t.trip_id for t in s2.list_trips()] == [trip_id]

def test_receipts_with_overrides_and_diff_only_save(tmp_path, law_fixture_text):
    s, traveler, trip_id, _ = _extracted(tmp_path, law_fixture_text)
    r = s.receipts(traveler, trip_id)[0]
    s.save_override(traveler, trip_id, r.receipt_id, {"amount": "48,200", "merchant": "코레일", "service_date": "2026-07-09", "nights": ""}, clear_warnings=["MISSING_BIZNO"])
    path = s.trip_dir(traveler, trip_id) / "overrides.yaml"
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {r.receipt_id: {"merchant": "코레일", "clear_warnings": ["MISSING_BIZNO"]}}
    assert s.receipts(traveler, trip_id)[0].merchant == "코레일"
    s.save_override(traveler, trip_id, r.receipt_id, {"merchant": "한국철도공사"}, clear_warnings=[])
    assert not path.exists() or yaml.safe_load(path.read_text(encoding="utf-8")) in (None, {})
    with pytest.raises(NotFound):
        s.save_override(traveler, trip_id, "nope-p1", {"amount": "1"}, clear_warnings=[])

def test_images_versions_and_law_cache(tmp_path, law_fixture_text):
    s, traveler, trip_id, clients = _extracted(tmp_path, law_fixture_text)
    rid = s.receipts(traveler, trip_id)[0].receipt_id
    assert s.images_for(traveler, trip_id)[rid] == [rid] and s.image_path(traveler, trip_id, rid).exists()
    with pytest.raises(InvalidName):
        s.image_path(traveler, trip_id, "../../x")
    assert s.law_book(date(2000, 1, 1)) is None
    run_batch(s.data_dir, s.out_dir, clients, summary=False)
    assert s.law_book(date.today()).current.mst == "287535"
    v = s.versions(traveler, trip_id)
    assert [x.version for x in v] == [1] and v[0].hwpx.exists() and v[0].verify_ok is True
    assert s.trip_status(traveler, trip_id).stage == "documented" and s.latest_result(traveler, trip_id).version == 1
    assert s.version_file(traveler, trip_id, 1, "evidence.hwpx").exists()
    with pytest.raises(InvalidName):
        s.version_file(traveler, trip_id, 1, "../latest.json")
    with pytest.raises(FileNotFoundError):
        s.version_file(traveler, trip_id, 7, "evidence.hwpx")


def test_status_hashes_are_cached_between_calls(tmp_path, law_fixture_text, monkeypatch):
    s, traveler, trip_id, _ = _extracted(tmp_path, law_fixture_text)
    s.list_trips()
    import receipt_evidence.workspace as ws
    monkeypatch.setattr(ws, "sha256_file", lambda p: pytest.fail("바뀌지 않은 파일을 다시 해시함"))
    assert TripService(s.data_dir, s.out_dir).list_trips()[0].stale is False

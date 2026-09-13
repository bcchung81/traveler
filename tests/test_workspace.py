# tests/test_workspace.py
import unicodedata
from datetime import date, datetime
from pathlib import Path
import yaml
from receipt_evidence.models import Category, Receipt
from receipt_evidence.workspace import (TripJob, apply_overrides, discover, dump_trip_yaml, load_overrides, load_traveler,
                                        propose_trip, resolve_trip)

def _touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"x")

def _tree(root: Path) -> Path:
    _touch(root / "loose.jpg")
    _touch(root / "정백철" / "stray.pdf")
    _touch(root / "정백철" / "2026-07-09_서울" / "a.jpg")
    _touch(root / "정백철" / "2026-08-03_부산" / "b.png")
    (root / "정백철" / "2026-09-01_빈폴더").mkdir(parents=True)
    _touch(root / "홍길동" / "2026-07-20_대전" / "c.pdf")
    return root

def test_discover_jobs_and_warnings(tmp_path):
    jobs, warnings = discover(_tree(tmp_path / "data"))
    assert [(j.traveler, j.trip_id) for j in jobs] == [("정백철", "2026-07-09_서울"), ("정백철", "2026-08-03_부산"), ("홍길동", "2026-07-20_대전")]
    assert any("loose.jpg" in w for w in warnings) and any("stray.pdf" in w for w in warnings) and any("2026-09-01_빈폴더" in w for w in warnings)

def test_discover_filters(tmp_path):
    jobs, _ = discover(_tree(tmp_path / "data"), travelers=["정백철"], trips=["2026-08-03_부산"])
    assert [(j.traveler, j.trip_id) for j in jobs] == [("정백철", "2026-08-03_부산")]

def test_load_traveler_default_and_yaml(tmp_path):
    d = tmp_path / "정백철"; d.mkdir()
    assert load_traveler(d).name == "정백철" and load_traveler(d).grade is None
    (d / "traveler.yaml").write_text("grade: 제2호\nworkplace_region: 나주\napproval: [담당, 팀장]\n", encoding="utf-8")
    p = load_traveler(d)
    assert p.grade == "제2호" and p.workplace_region == "나주" and p.name == "정백철" and p.approval == ["담당", "팀장"]

def _rail(rid, day, o, d):
    return Receipt(receipt_id=rid, image_id=rid, category=Category.RAIL, amount=48200, service_date=day, origin=o, destination=d)

def test_resolve_trip_prefers_yaml(tmp_path):
    trav = tmp_path / "정백철"; trip_dir = trav / "2026-07-09_서울"; trip_dir.mkdir(parents=True)
    (trav / "traveler.yaml").write_text("grade: 제2호\n", encoding="utf-8")
    (trip_dir / "trip.yaml").write_text("start_date: 2026-07-09\nend_date: 2026-07-10\ndestination_region: 서울\npurpose: 회의\n", encoding="utf-8")
    job = TripJob("정백철", "2026-07-09_서울", trav, trip_dir)
    t = resolve_trip(job, load_traveler(trav), [])
    assert t.proposed is False and t.days == 2 and t.purpose == "회의" and t.trip_id == "2026-07-09_서울"
    assert t.traveler_name == "정백철" and t.grade == "제2호"

def test_propose_trip_ignores_payment_only_dates():
    stay = Receipt(receipt_id="s", image_id="s", category=Category.LODGING, amount=100000, paid_at=datetime(2026, 8, 21, 16, 44))
    rs = [_rail("r1", date(2026, 7, 9), "나주", "용산"), _rail("r2", date(2026, 7, 10), "용산", "나주"), stay]
    t = propose_trip("2026-07-09_서울", rs, {"traveler_name": "정백철", "trip_id": "2026-07-09_서울"})
    assert t.proposed and (t.start_date, t.end_date) == (date(2026, 7, 9), date(2026, 7, 10))
    assert t.destination_region == "서울" and t.route_stations == ["나주", "용산"] and len(t.proposal_basis) == 3
    y = yaml.safe_load(dump_trip_yaml(t))
    assert y["start_date"] == "2026-07-09" and y["destination_region"] == "서울" and "traveler_name" not in y

def test_propose_trip_bad_folder_name():
    t = propose_trip("서울출장", [], {"traveler_name": "x", "trip_id": "서울출장"})
    assert t.proposed and t.start_date is None and t.days is None and t.destination_region == ""

def test_overrides_merge_and_clear_warnings(tmp_path):
    (tmp_path / "overrides.yaml").write_text("s:\n  service_date: 2026-07-09\n  region: 서울\n  clear_warnings: [MISSING_BIZNO]\n", encoding="utf-8")
    stay = Receipt(receipt_id="s", image_id="s", category=Category.LODGING, amount=100000, warnings=["MISSING_BIZNO", "MISSING_APPROVAL"])
    out = apply_overrides([stay, _rail("r1", date(2026, 7, 9), "나주", "용산")], load_overrides(tmp_path))
    assert out[0].service_date == date(2026, 7, 9) and out[0].region == "서울" and out[0].warnings == ["MISSING_APPROVAL"]
    assert out[0].raw["overrides"]["region"] == "서울" and out[1].receipt_id == "r1" and load_overrides(tmp_path / "none") == {}

def test_nfd_folder_names_from_finder_are_normalized(tmp_path):
    nfd = lambda s: unicodedata.normalize("NFD", s)  # macOS Finder가 만든 한글 이름은 자모 분리형(NFD)
    root = tmp_path / "data"
    _touch(root / nfd("정백철") / nfd("2026-07-09_서울") / "a.jpg")
    jobs, _ = discover(root, travelers=["정백철"], trips=[nfd("2026-07-09_서울")])
    assert [(j.traveler, j.trip_id) for j in jobs] == [("정백철", "2026-07-09_서울")]
    assert load_traveler(jobs[0].traveler_dir).name == "정백철"
    assert propose_trip(nfd("2026-07-09_서울"), [], {"traveler_name": "x", "trip_id": "t"}).destination_region == "서울"

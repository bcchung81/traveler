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
    assert t.destination_region == "서울" and t.route_stations == ["나주", "용산"] and "폴더 이름" in t.proposal_basis
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

import json, os, threading
import pytest
from receipt_evidence.workspace import BusyError, HashCache, NotFound, clear_warnings, out_lock, trip_file_owners

def test_overrides_revalidate_changed_amount_and_clear_last(tmp_path):
    t = tmp_path / "t.txt"; t.write_text("결제금액 48,200원 승인번호 7000001", encoding="utf-8")
    r = _rail("r1", date(2026, 7, 9), "나주", "용산").model_copy(update={"approval_no": "7000001", "transcript_path": str(t)})
    typo = apply_overrides([r], {"r1": {"amount": 42800}})[0]
    assert typo.amount == 42800 and "AMOUNT_NOT_IN_TRANSCRIPT" in typo.warnings  # 사용자 수정값도 원문과 대조한다
    ok = apply_overrides([r], {"r1": {"amount": 42800, "clear_warnings": ["AMOUNT_NOT_IN_TRANSCRIPT"]}})[0]
    assert "AMOUNT_NOT_IN_TRANSCRIPT" not in ok.warnings
    later = clear_warnings([ok.model_copy(update={"warnings": ["DUP_ACROSS_TRIPS", "LODGING_OVERLAP"]})], {"r1": {"clear_warnings": ["DUP_ACROSS_TRIPS"]}})
    assert later[0].warnings == ["LODGING_OVERLAP"]

def test_overrides_recompute_duplicate_approval(tmp_path):
    a = _rail("a", date(2026, 7, 9), "나주", "용산").model_copy(update={"approval_no": "1", "warnings": ["DUP_APPROVAL"]})
    b = _rail("b", date(2026, 7, 10), "용산", "나주").model_copy(update={"approval_no": "1", "warnings": ["DUP_APPROVAL"]})
    out = apply_overrides([a, b], {"b": {"approval_no": "2"}})
    assert "DUP_APPROVAL" not in out[0].warnings and "DUP_APPROVAL" not in out[1].warnings

def test_hash_cache_reuses_until_file_changes(tmp_path, monkeypatch):
    f = tmp_path / "a.jpg"; f.write_bytes(b"one")
    calls = []
    import receipt_evidence.workspace as ws
    real = ws.sha256_file
    monkeypatch.setattr(ws, "sha256_file", lambda p: calls.append(p) or real(p))
    hc = HashCache(tmp_path / "hashes.json")
    first = hc.sha256(f); hc.flush()
    assert HashCache(tmp_path / "hashes.json").sha256(f) == first and len(calls) == 1
    f.write_bytes(b"two-changed"); os.utime(f, ns=(1, 2_000_000_000))
    assert HashCache(tmp_path / "hashes.json").sha256(f) != first and len(calls) == 2

def test_out_lock_rejects_second_holder(tmp_path):
    with out_lock(tmp_path):
        with pytest.raises(BusyError, match="실행 중"):
            with out_lock(tmp_path):
                pass
    with out_lock(tmp_path):  # 풀린 뒤에는 다시 잡힌다
        pass

def test_trip_file_owners_cover_all_trips(tmp_path):
    root = _tree(tmp_path / "data")
    owners = trip_file_owners(root, HashCache(tmp_path / "h.json"))
    keys = {k for ks in owners.values() for k in ks}
    assert {"정백철/2026-07-09_서울", "정백철/2026-08-03_부산", "홍길동/2026-07-20_대전"} == keys
    assert len(owners) == 1  # _tree는 모든 파일 내용이 b"x"로 같다 → 한 해시에 세 출장
    assert issubclass(NotFound, LookupError)

from receipt_evidence.workspace import (STAGING_PREFIX, is_staging, move_trip, resolve_moved, staging_trip_id, suggest_trip,
                                        unique_trip_id)

def _leg(rid, day, o, d, cat=Category.RAIL, paid=None):
    return Receipt(receipt_id=rid, image_id=rid, category=cat, amount=48200, service_date=day, origin=o, destination=d, paid_at=paid,
                   region="대전광역시")  # 철도 영수증 region은 코레일 본사 주소 — 출장지로 쓰면 안 된다

def test_suggest_round_trip_from_rail_legs_ignores_payment_date_and_receipt_region():
    rs = [_leg("b", date(2026, 7, 10), "용산", "나주", paid=datetime(2026, 6, 11, 8, 31)), _leg("a", date(2026, 7, 9), "나주", "용산")]
    s = suggest_trip("_새정산-20260913-223105", rs)
    assert (s["start_date"].value, s["end_date"].value) == (date(2026, 7, 9), date(2026, 7, 10))
    assert s["destination_region"].value == "서울" and "용산" in s["destination_region"].basis
    assert s["workplace_region"].value == "나주" and s["workplace_region"].guessed
    assert s["route_stations"].value == ["나주", "용산"] and "lodging_region" not in s

def test_suggest_return_ticket_only_uses_known_workplace():
    s = suggest_trip("_새정산-x", [_leg("r", date(2026, 7, 10), "용산", "나주")], workplace="나주")
    assert s["destination_region"].value == "서울" and s["start_date"].value == date(2026, 7, 10) and "workplace_region" not in s

def test_suggest_unknown_station_lodging_dates_and_payer():
    stay = Receipt(receipt_id="s", image_id="s", category=Category.LODGING, amount=100000, service_date=date(2026, 8, 27),
                   service_end_date=date(2026, 8, 29), payer_name="정백철", region="서울 강남구 테헤란로")  # 결제대행사 주소
    s = suggest_trip("_새정산-x", [_leg("a", date(2026, 8, 27), "나주", "정동진"), stay])
    assert s["destination_region"].value == "정동진" and s["destination_region"].guessed  # 표에 없는 역은 역명 그대로, 확인 권장
    assert (s["start_date"].value, s["end_date"].value) == (date(2026, 8, 27), date(2026, 8, 29))
    assert s["payer_names"].value == ["정백철"]
    assert "lodging_region" not in s  # 숙박 영수증에 region이 있으면 추정하지 않는다(판정은 영수증 값을 쓴다)
    nostay = stay.model_copy(update={"region": None})
    s2 = suggest_trip("_새정산-x", [_leg("a", date(2026, 8, 27), "나주", "용산"), nostay])
    assert s2["lodging_region"].value == "서울" and s2["lodging_region"].guessed

def test_suggest_prefers_user_named_folder_and_handles_no_receipts():
    s = suggest_trip("2026-07-09_부산", [_leg("a", date(2026, 7, 9), "나주", "용산")])
    assert s["destination_region"].value == "부산" and "폴더" in s["destination_region"].basis
    assert suggest_trip("_새정산-x", []) == {}

def test_staging_names_unique_ids_and_move_trip(tmp_path):
    data, out = tmp_path / "data", tmp_path / "out"
    sid = staging_trip_id(datetime(2026, 9, 13, 22, 31, 5))
    assert sid == f"{STAGING_PREFIX}20260913-223105" and is_staging(sid) and not is_staging("2026-07-09_서울")
    old = data / "정백철" / sid; old.mkdir(parents=True); (old / "k.png").write_bytes(b"x")
    (data / "정백철" / "2026-07-09_서울").mkdir()
    assert unique_trip_id(data, "정백철", "2026-07-09_서울") == "2026-07-09_서울_2"
    work = out / "정백철" / sid / "work"; (work / "transcripts").mkdir(parents=True)
    (work / "manifest.json").write_text(json.dumps([{"source_path": str(old / "k.png"), "png_path": str(work / "images/a.png")}], ensure_ascii=False), encoding="utf-8")
    new = move_trip(data, out, "정백철", sid, "2026-07-09_서울_2")
    assert new == "2026-07-09_서울_2" and not old.exists() and (data / "정백철" / new / "k.png").exists()
    m = json.loads((out / "정백철" / new / "work" / "manifest.json").read_text(encoding="utf-8"))[0]
    assert m["source_path"] == str(data / "정백철" / new / "k.png") and m["png_path"] == str(out / "정백철" / new / "work/images/a.png")
    assert resolve_moved(out, "정백철", sid) == new and resolve_moved(out, "정백철", new) is None
    move_trip(data, out, "정백철", new, "2026-07-10_서울")
    assert resolve_moved(out, "정백철", sid) == "2026-07-10_서울"  # 여러 번 옮겨도 최종 주소로
    (out / "정백철" / "2026-07-10_서울" / "latest.json").write_text('{"version": 1, "fingerprint": "f"}', encoding="utf-8")
    with pytest.raises(ValueError, match="서류"):
        move_trip(data, out, "정백철", "2026-07-10_서울", "2026-07-11_서울")

def test_trip_yaml_without_period_keeps_proposal(tmp_path):
    # 출장 목적만 먼저 저장한 경우: 기간이 없으면 확정이 아니다 — 영수증 제안값을 유지하고 적힌 값은 우선한다
    trav = tmp_path / "정백철"; trip_dir = trav / "2026-07-09_서울"; trip_dir.mkdir(parents=True)
    (trip_dir / "trip.yaml").write_text("purpose: 회의\nofficial_vehicle: false\n", encoding="utf-8")
    job = TripJob("정백철", "2026-07-09_서울", trav, trip_dir)
    rs = [_rail("r1", date(2026, 7, 9), "나주", "용산"), _rail("r2", date(2026, 7, 10), "용산", "나주")]
    t = resolve_trip(job, load_traveler(trav), rs)
    assert t.proposed and (t.start_date, t.end_date) == (date(2026, 7, 9), date(2026, 7, 10)) and t.purpose == "회의"
    (trip_dir / "trip.yaml").write_text("purpose: 회의\nstart_date: 2026-07-09\nend_date: 2026-07-11\n", encoding="utf-8")
    t = resolve_trip(job, load_traveler(trav), rs)
    assert not t.proposed and t.end_date == date(2026, 7, 11)

def test_period_reliable_needs_round_trip_or_full_stay():
    out_leg, back_leg = _leg("a", date(2026, 7, 9), "나주", "용산"), _leg("b", date(2026, 7, 10), "용산", "나주")
    base = {"traveler_name": "정백철", "trip_id": "x"}
    assert propose_trip("_새정산-x", [out_leg, back_leg], base).period_reliable  # 가는 편·오는 편
    assert not propose_trip("_새정산-x", [out_leg], base).period_reliable  # 한쪽 표만
    assert not propose_trip("2026-07-09_서울", [], base).period_reliable  # 폴더 날짜만
    stay = Receipt(receipt_id="s", image_id="s", category=Category.LODGING, amount=90000, service_date=date(2026, 7, 9), service_end_date=date(2026, 7, 10))
    assert propose_trip("_새정산-x", [out_leg, stay], base).period_reliable  # 숙박 체크인·체크아웃
    assert not propose_trip("_새정산-x", [stay.model_copy(update={"service_end_date": None})], base).period_reliable
    assert propose_trip("_새정산-x", [_leg("r", date(2026, 7, 10), "용산", "나주"), _leg("o", date(2026, 7, 9), "나주", "용산")],
                        base | {"workplace_region": "나주"}).period_reliable

from receipt_evidence.workspace import TRASH_DIR, list_trash, purge_trash, restore_trip, trash_trip

def _trip_with_out(tmp_path, trip_id="2026-07-09_서울"):
    data, out = tmp_path / "data", tmp_path / "out"
    d = data / "정백철" / trip_id; d.mkdir(parents=True); (d / "k.png").write_bytes(b"x"); (d / "trip.yaml").write_text("purpose: 회의\n", encoding="utf-8")
    (data / "정백철" / "traveler.yaml").write_text("grade: 제2호\n", encoding="utf-8")
    work = out / "정백철" / trip_id / "work"; work.mkdir(parents=True)
    (work / "manifest.json").write_text(json.dumps([{"source_path": str(d / "k.png"), "png_path": str(work / "images/a.png")}], ensure_ascii=False), encoding="utf-8")
    (out / "정백철" / trip_id / "latest.json").write_text('{"version": 2, "fingerprint": "f"}', encoding="utf-8")
    return data, out, d

def test_trash_restore_and_purge(tmp_path):
    data, out, d = _trip_with_out(tmp_path)
    tid = trash_trip(data, out, "정백철", "2026-07-09_서울", now=datetime(2026, 9, 14, 10, 0, 0))
    assert tid == "20260914-100000_정백철_2026-07-09_서울" and not d.exists() and not (out / "정백철" / "2026-07-09_서울").exists()
    assert (data / TRASH_DIR / tid / "data" / "k.png").exists() and (out / TRASH_DIR / tid / "latest.json").exists()
    assert (data / "정백철" / "traveler.yaml").exists()  # 출장자 설정은 남긴다
    assert discover(data)[0] == []  # 휴지통은 목록·CLI에서 빠진다
    [entry] = list_trash(data)
    assert (entry["id"], entry["traveler"], entry["trip_id"], entry["files"], entry["version"]) == (tid, "정백철", "2026-07-09_서울", 1, 2)
    assert restore_trip(data, out, tid) == ("정백철", "2026-07-09_서울") and d.exists() and list_trash(data) == []
    assert (out / "정백철" / "2026-07-09_서울" / "latest.json").exists()
    tid2 = trash_trip(data, out, "정백철", "2026-07-09_서울", now=datetime(2026, 9, 14, 10, 5, 0))
    purge_trash(data, out, tid2)
    assert list_trash(data) == [] and not (out / TRASH_DIR / tid2).exists()
    with pytest.raises(NotFound):
        trash_trip(data, out, "정백철", "2026-07-09_서울")

def test_restore_with_same_name_goes_to_suffix_and_fixes_paths(tmp_path):
    data, out, d = _trip_with_out(tmp_path)
    tid = trash_trip(data, out, "정백철", "2026-07-09_서울", now=datetime(2026, 9, 14, 11, 0, 0))
    d.mkdir(parents=True); (d / "new.png").write_bytes(b"y")  # 지운 뒤 같은 이름으로 새로 만든 출장
    assert restore_trip(data, out, tid) == ("정백철", "2026-07-09_서울_2")
    m = json.loads((out / "정백철" / "2026-07-09_서울_2" / "work" / "manifest.json").read_text(encoding="utf-8"))[0]
    assert m["source_path"] == str(data / "정백철" / "2026-07-09_서울_2" / "k.png") and (d / "new.png").exists()
    with pytest.raises(NotFound):
        restore_trip(data, out, "20990101-000000_없음_x")

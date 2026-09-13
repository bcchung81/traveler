# tests/test_pipeline.py
import json, shutil
from datetime import date, timedelta
from pathlib import Path
import pytest
from helpers import TRAVELER, ColorVlm, color_png, doc_fake, law_from, rail, spec
from receipt_evidence.models import Verdict
from receipt_evidence.pipeline import Clients, RunOptions, run_batch

def test_single_trip_versions_and_incremental_submission(tmp_path, law_fixture_text):
    data, out = tmp_path / "data", tmp_path / "out"
    trav = data / "정백철"; trip_dir = trav / "2026-07-09_서울"; trip_dir.mkdir(parents=True)
    (trav / "traveler.yaml").write_text(TRAVELER, encoding="utf-8")
    (trip_dir / "trip.yaml").write_text("start_date: 2026-07-09\nend_date: 2026-07-10\ndestination_region: 서울\nroute_stations: [나주, 용산]\n", encoding="utf-8")
    specs = {(10, 20, 30): rail(1, date(2026, 7, 9), "나주", "용산"), (40, 50, 60): rail(2, date(2026, 7, 10), "용산", "나주"),
             (70, 80, 90): spec("숙박", 100000, "68325420", merchant="(주)예시숙박", service_date="2026-07-09", region="서울", nights=1)}
    color_png(trip_dir / "k1.png", (10, 20, 30)); color_png(trip_dir / "k2.png", (40, 50, 60))
    vlm = ColorVlm(specs)
    clients = Clients(vlm=vlm, law=law_from(law_fixture_text), doc=doc_fake())

    r1 = run_batch(data, out, clients, run_id="b1").results[0]
    assert (r1.version, r1.skipped, r1.cache_misses, r1.verify_ok) == (1, False, 2, True)
    assert r1.totals == {"claimed": 96400, "approved": 196400, "review": 0}
    calls = vlm.calls

    r2 = run_batch(data, out, clients, run_id="b2").results[0]
    assert (r2.version, r2.skipped, r2.cache_hits) == (1, True, 2) and vlm.calls == calls

    color_png(trip_dir / "stay.png", (70, 80, 90))
    r3 = run_batch(data, out, clients, run_id="b3").results[0]
    assert (r3.version, r3.skipped, r3.cache_hits, r3.cache_misses) == (2, False, 2, 1)
    assert r3.totals["approved"] == 296400 and vlm.calls == calls + 2
    assert "v1 → v2" in Path(r3.changes_md_path).read_text(encoding="utf-8") and "숙박비" in Path(r3.changes_md_path).read_text(encoding="utf-8")
    trip_out = out / "정백철" / "2026-07-09_서울"
    assert json.loads((trip_out / "latest.json").read_text())["version"] == 2 and (trip_out / "v1" / "evidence.hwpx").exists()
    assert len(list((trip_out / "v2" / "attachments").glob("*.jpg"))) == 3 and (out / "summary-b3.md").exists()

def test_batch_travelers_trips_scale_and_cross_trip_duplicate(tmp_path, law_fixture_text):
    data, out = tmp_path / "data", tmp_path / "out"
    plan = [("정백철", "2026-07-09_서울", date(2026, 7, 9), "start_date: 2026-07-09\nend_date: 2026-07-10\ndestination_region: 서울\n"),
            ("정백철", "2026-08-03_부산", date(2026, 8, 3), "start_date: 2026-08-03\nend_date: 2026-08-04\ndestination_region: 부산\n"),
            ("홍길동", "2026-07-20_대전", date(2026, 7, 20), "start_date: 2026-07-20\nend_date: 2026-07-21\ndestination_region: 대전\n"),
            ("홍길동", "2026-09-01_광주", date(2026, 9, 1), None)]
    specs, n = {}, 0
    for traveler, trip_id, day, trip_yaml in plan:
        trip_dir = data / traveler / trip_id; trip_dir.mkdir(parents=True)
        (data / traveler / "traveler.yaml").write_text(TRAVELER, encoding="utf-8")
        if trip_yaml:
            (trip_dir / "trip.yaml").write_text(trip_yaml, encoding="utf-8")
        for k in range(3):
            n += 1
            rgb = (n * 7 % 256, 100, 200 - n)
            specs[rgb] = rail(n, day + timedelta(days=k % 2), "나주", "서울역")
            color_png(trip_dir / f"r{k}.png", rgb)
    shutil.copy(data / "정백철" / "2026-07-09_서울" / "r0.png", data / "홍길동" / "2026-07-20_대전" / "dup.png")

    res = run_batch(data, out, Clients(vlm=ColorVlm(specs), law=law_from(law_fixture_text), doc=doc_fake()), run_id="b1", opts=RunOptions(workers=3))
    by = {(r.traveler, r.trip_id): r for r in res.results}
    assert len(res.results) == 4 and all(r.error is None and r.version == 1 and r.verify_ok for r in res.results)
    assert sum(r.cache_misses for r in res.results) == 12 and sum(r.cache_hits for r in res.results) == 1
    assert sum(len(r.receipts) for r in res.results) == 13
    dup = sorted(k for k, r in by.items() if any("DUP_ACROSS_TRIPS" in x.warnings for x in r.receipts))
    assert dup == [("정백철", "2026-07-09_서울"), ("홍길동", "2026-07-20_대전")]
    proposed = by[("홍길동", "2026-09-01_광주")]
    assert proposed.trip.proposed and all(d.verdict is Verdict.REVIEW for d in proposed.decisions if d.receipt_id is None)
    assert (out / "홍길동" / "2026-09-01_광주" / "work" / "trip.proposed.yaml").exists()
    assert Path(res.summary_md_path).read_text(encoding="utf-8").count("| v1 |") == 4 and Path(res.summary_json_path).exists()

def test_no_trip_folders_raises_with_guidance(tmp_path, law_fixture_text):
    data = tmp_path / "data"; data.mkdir(); color_png(data / "loose.png", (1, 2, 3))
    with pytest.raises(ValueError, match="출장자"):
        run_batch(data, tmp_path / "out", Clients(vlm=ColorVlm({}), law=law_from(law_fixture_text), doc=doc_fake()))

def test_unhealthy_vlm_only_matters_on_cache_miss(tmp_path, law_fixture_text):
    data, out = tmp_path / "data", tmp_path / "out"
    trip_dir = data / "정백철" / "2026-07-09_서울"; trip_dir.mkdir(parents=True)
    specs = {(10, 20, 30): rail(1, date(2026, 7, 9), "나주", "용산"), (40, 50, 60): rail(2, date(2026, 7, 10), "용산", "나주")}
    color_png(trip_dir / "k1.png", (10, 20, 30))
    run_batch(data, out, Clients(vlm=ColorVlm(specs), law=law_from(law_fixture_text), doc=doc_fake()), run_id="a")
    down = ColorVlm(specs, healthy=False)
    assert run_batch(data, out, Clients(vlm=down, law=law_from(law_fixture_text), doc=doc_fake()), run_id="b").results[0].skipped
    color_png(trip_dir / "k2.png", (40, 50, 60))
    with pytest.raises(RuntimeError, match="llama-server"):
        run_batch(data, out, Clients(vlm=down, law=law_from(law_fixture_text), doc=doc_fake()), run_id="c")

def test_one_trip_failure_does_not_stop_batch(tmp_path, law_fixture_text):
    data, out = tmp_path / "data", tmp_path / "out"
    specs = {}
    for trip_id, rgb, day in (("2026-07-09_서울", (10, 20, 30), date(2026, 7, 9)), ("2026-08-03_부산", (40, 50, 60), date(2026, 8, 3))):
        d = data / "정백철" / trip_id; d.mkdir(parents=True); color_png(d / "k.png", rgb)
        specs[rgb] = rail(rgb[0], day, "나주", "부산")
    res = run_batch(data, out, Clients(vlm=ColorVlm(specs), law=law_from(law_fixture_text), doc=doc_fake(fail_on="2026-08-03_부산")), run_id="x")
    by = {r.trip_id: r for r in res.results}
    assert by["2026-07-09_서울"].error is None and "kordoc 실패 재현" in by["2026-08-03_부산"].error
    assert "오류" in Path(res.summary_md_path).read_text(encoding="utf-8")

from receipt_evidence.mcp_client import FakeToolCaller
from receipt_evidence.pipeline import extract_trip
from receipt_evidence.workspace import BusyError, out_lock

class _Offline(FakeToolCaller):
    def call_many(self, calls):
        raise RuntimeError("MCP 서버 시작 실패(npx): getaddrinfo ENOTFOUND registry.npmjs.org")

def _one_trip(tmp_path, trip_id="2026-07-09_서울", rgb=(10, 20, 30), day=date(2026, 7, 9)):
    data = tmp_path / "data"; d = data / "정백철" / trip_id; d.mkdir(parents=True)
    (data / "정백철" / "traveler.yaml").write_text(TRAVELER, encoding="utf-8")
    (d / "trip.yaml").write_text(f"start_date: {day}\nend_date: {day + timedelta(days=1)}\ndestination_region: 서울\n", encoding="utf-8")
    color_png(d / "k.png", rgb)
    return data, d, {rgb: rail(rgb[0], day, "나주", "용산")}

def test_law_is_resolved_before_extraction(tmp_path, law_fixture_text):
    data, _, specs = _one_trip(tmp_path)
    vlm = ColorVlm(specs)
    law = law_from(law_fixture_text)
    orig = law.handlers["search_law"]
    law.handlers["search_law"] = lambda a: (vlm.calls == 0 or pytest.fail("영수증을 읽기 전에 규정을 먼저 확보해야 함")) and orig(a)
    assert run_batch(data, tmp_path / "out", Clients(vlm=vlm, law=law, doc=doc_fake()), run_id="o").results[0].error is None

def test_offline_uses_held_law_and_says_so(tmp_path, law_fixture_text):
    data, _, specs = _one_trip(tmp_path)
    res = run_batch(data, tmp_path / "out", Clients(vlm=ColorVlm(specs), law=_Offline({}), doc=doc_fake()), run_id="off")
    r = res.results[0]
    assert r.error is None and r.version == 1 and r.law_mst == "287535" and any("인터넷" in x for x in r.law_notes)
    assert any("인터넷" in x for x in res.notices) and "인터넷에 연결되지 않아" in Path(r.report_md_path).read_text(encoding="utf-8")

def test_broken_file_in_one_trip_does_not_stop_batch(tmp_path, law_fixture_text):
    data, _, specs = _one_trip(tmp_path)
    broken = data / "정백철" / "2026-08-03_부산"; broken.mkdir(parents=True); (broken / "bad.pdf").write_bytes(b"not a pdf")
    res = run_batch(data, tmp_path / "out", Clients(vlm=ColorVlm(specs), law=law_from(law_fixture_text), doc=doc_fake()), run_id="br")
    by = {r.trip_id: r for r in res.results}
    assert [r.trip_id for r in res.results] == ["2026-07-09_서울", "2026-08-03_부산"]
    assert by["2026-07-09_서울"].error is None and by["2026-08-03_부산"].error

def test_single_trip_run_sees_duplicates_in_other_trips(tmp_path, law_fixture_text):
    data, d, specs = _one_trip(tmp_path)
    other = data / "정백철" / "2026-08-03_부산"; other.mkdir(parents=True); shutil.copy(d / "k.png", other / "same.png")
    clients = Clients(vlm=ColorVlm(specs), law=law_from(law_fixture_text), doc=doc_fake())
    r = run_batch(data, tmp_path / "out", clients, trips=["2026-07-09_서울"], run_id="s1", summary=False).results[0]
    rec = r.receipts[0]
    assert "DUP_ACROSS_TRIPS" in rec.warnings and rec.raw["dup_across_trips"] == ["정백철/2026-08-03_부산"]
    assert any(x.verdict is Verdict.REVIEW and "다른 출장" in x.reasons[0] for x in r.decisions if x.receipt_id)
    (d / "overrides.yaml").write_text(f"{rec.receipt_id}:\n  clear_warnings: [DUP_ACROSS_TRIPS]\n", encoding="utf-8")
    r2 = run_batch(data, tmp_path / "out", clients, trips=["2026-07-09_서울"], run_id="s2", summary=False).results[0]
    assert "DUP_ACROSS_TRIPS" not in r2.receipts[0].warnings and r2.decisions[0].verdict is Verdict.PAY

def test_concurrent_runs_are_rejected(tmp_path, law_fixture_text):
    data, _, specs = _one_trip(tmp_path)
    clients = Clients(vlm=ColorVlm(specs), law=law_from(law_fixture_text), doc=doc_fake())
    with out_lock(tmp_path / "out"):
        with pytest.raises(BusyError):
            run_batch(data, tmp_path / "out", clients)
        with pytest.raises(BusyError):
            extract_trip(data, tmp_path / "out", clients, "정백철", "2026-07-09_서울")

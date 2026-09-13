# tests/test_versioning.py
import json
from datetime import date
from pathlib import Path
from receipt_evidence.models import Receipt
from receipt_evidence.rules import decide_all
from receipt_evidence.versioning import diff_markdown, fingerprint, load_decisions, plan_version, read_latest, write_latest

GOLD = [Receipt(**d) for d in json.loads((Path(__file__).parent / "fixtures/golden/receipts.json").read_text(encoding="utf-8"))]

def test_fingerprint_changes_only_with_decision_inputs(trip, law_snapshot):
    fp = fingerprint(GOLD, trip, law_snapshot)
    assert fp == fingerprint(list(reversed(GOLD)), trip, law_snapshot)
    assert fp == fingerprint([g.model_copy(update={"transcript_path": "elsewhere.txt", "confidence": 0.5}) for g in GOLD], trip, law_snapshot)
    assert fp != fingerprint(GOLD[:2], trip, law_snapshot)
    assert fp != fingerprint(GOLD, trip.model_copy(update={"lodging_region": "서울"}), law_snapshot)
    assert fp != fingerprint(GOLD, trip, law_snapshot.model_copy(update={"mst": "999999"}))

def test_plan_version_flow(tmp_path):
    assert plan_version(tmp_path, "a") == (1, True)
    write_latest(tmp_path, 1, "a")
    assert plan_version(tmp_path, "a") == (1, False) and plan_version(tmp_path, "a", force=True) == (2, True)
    assert plan_version(tmp_path, "b") == (2, True) and read_latest(tmp_path) == {"version": 1, "fingerprint": "a"}

def test_diff_markdown_lists_added_and_totals(trip, law_snapshot, tmp_path):
    v1 = decide_all(GOLD[:2], trip, law_snapshot)
    fixed_stay = GOLD[2].model_copy(update={"service_date": date(2026, 7, 9), "region": "서울"})
    v2 = decide_all(GOLD[:2] + [fixed_stay], trip, law_snapshot)
    md = diff_markdown(v1, v2, 1, 2)
    assert "# 변경 내역 v1 → v2" in md and "숙박비(stay)" in md and "인정 196,400 → 296,400" in md and "## 삭제\n- 없음" in md
    (tmp_path / "d.json").write_text(json.dumps([d.model_dump(mode="json") for d in v2]), encoding="utf-8")
    assert load_decisions(tmp_path / "d.json") == v2 and load_decisions(tmp_path / "none.json") == []

def test_fingerprint_includes_report_version(trip, law_snapshot, monkeypatch):
    import receipt_evidence.versioning as v
    fp = fingerprint(GOLD, trip, law_snapshot)
    monkeypatch.setattr(v, "REPORT_VERSION", "changed")
    assert fingerprint(GOLD, trip, law_snapshot) != fp  # 서식을 고치면 같은 입력이라도 새 버전 문서를 만든다

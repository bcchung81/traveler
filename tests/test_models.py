# tests/test_models.py
from datetime import date
from receipt_evidence.models import (Category, Decision, PipelineResult, RateTable, Receipt, TravelerProfile, TripConfig, Verdict)

def test_receipt_defaults():
    r = Receipt(receipt_id="r1", image_id="i1")
    assert r.category is Category.UNKNOWN and r.amount is None and r.warnings == [] and r.confidence == 1.0 and r.sha256 == ""

def test_trip_days():
    t = TripConfig(traveler_name="정백철", start_date=date(2026, 7, 9), end_date=date(2026, 7, 10))
    assert t.days == 2
    assert TripConfig(traveler_name="x").days is None

def test_rate_table_roundtrip():
    rt = RateTable(grade="제2호", rail="실비(일반실)", ship="실비(2등급)", air="실비", car="실비",
                   daily_allowance=25000, lodging="실비", lodging_caps={"서울특별시": 100000}, meal_allowance=25000)
    assert RateTable.model_validate_json(rt.model_dump_json()) == rt

def test_decision_verdict_value():
    d = Decision(receipt_id=None, item="일비", claimed_amount=0, approved_amount=50000, verdict=Verdict.PAY, basis=[], reasons=[])
    assert d.model_dump()["verdict"] == "지급"

def test_traveler_and_result_defaults():
    p = TravelerProfile(name="정백철")
    assert p.grade is None and p.approval == []
    t = TripConfig(traveler_name="정백철", trip_id="2026-07-09_서울")
    assert t.proposed is False and t.proposal_basis == []
    r = PipelineResult(run_id="r", trip=t, law_mst="1", law_effective=date(2026, 7, 1), receipts=[], decisions=[],
                       totals={}, review_items=[], report_md_path="")
    assert r.skipped is False and r.version is None and r.error is None and r.cache_hits == 0

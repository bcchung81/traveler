# tests/test_rules.py
import json
from datetime import date
from pathlib import Path
from receipt_evidence.models import Receipt, Category, Verdict
from receipt_evidence.rules import (RULES, allowance_rows, apply_cross_checks, decide_all, decide_receipt, mark_cross_trip_duplicates,
                                    region_key, review_items, totals)

GOLD = [Receipt(**d) for d in json.loads((Path(__file__).parent / "fixtures/golden/receipts.json").read_text(encoding="utf-8"))]

def test_region_key():
    assert region_key("서울 강남구") == "서울특별시" and region_key("부산광역시") == "광역시" and region_key("광주") == "광역시"
    assert region_key("전남 나주시") == "그 밖의 지역" and region_key("경기도 수원") == "그 밖의 지역"

def test_golden_three(trip, law_snapshot):
    ds = decide_all(GOLD, trip, law_snapshot)
    by = {d.receipt_id: d for d in ds if d.receipt_id}
    assert by["ktx1"].verdict is Verdict.PAY and by["ktx1"].approved_amount == 48200 and "별표2 제2호 철도운임 실비(일반실)" in by["ktx1"].basis
    assert by["ktx2"].verdict is Verdict.PAY
    assert by["stay"].verdict is Verdict.REVIEW and by["stay"].approved_amount == 0 and any("출장기간" in r for r in by["stay"].reasons)
    items = {d.item: d for d in ds if d.receipt_id is None}
    assert items["일비"].approved_amount == 50000 and items["식비"].approved_amount == 50000
    # 청구 = 영수증 48,200+48,200+100,000 / 인정 = 철도 96,400 + 일비 50,000 + 식비 50,000 / 확인필요 = 숙박 100,000
    assert totals(ds) == {"claimed": 196400, "approved": 196400, "review": 100000}
    assert any("stay" in x for x in review_items(ds, GOLD))

def test_lodging_cap_and_over_cap(trip, law_snapshot):
    stay = GOLD[2].model_copy(update={"service_date": date(2026, 7, 9), "region": "서울", "amount": 150000, "warnings": []})
    d = decide_receipt(stay, trip, law_snapshot)
    assert d.verdict is Verdict.REDUCED and d.approved_amount == 100000
    d2 = decide_receipt(stay, trip.model_copy(update={"over_cap_reason": "행사장 인근 만실"}), law_snapshot)
    assert d2.verdict is Verdict.REDUCED and d2.approved_amount == 130000 and "제16조제1항 단서" in d2.basis
    d3 = decide_receipt(stay.model_copy(update={"amount": 120000}), trip.model_copy(update={"over_cap_reason": "만실"}), law_snapshot)
    assert d3.verdict is Verdict.PAY and d3.approved_amount == 120000

def test_lodging_region_unknown_and_grade1(trip, law_snapshot):
    stay = GOLD[2].model_copy(update={"service_date": date(2026, 7, 9), "warnings": []})
    assert decide_receipt(stay, trip, law_snapshot).verdict is Verdict.REVIEW
    assert decide_receipt(stay, trip.model_copy(update={"lodging_region": "서울"}), law_snapshot).verdict is Verdict.PAY
    assert decide_receipt(stay, trip.model_copy(update={"grade": "제1호"}), law_snapshot).approved_amount == 100000

def test_rail_special_class_and_route(trip, law_snapshot):
    sp = GOLD[0].model_copy(update={"seat_class": "특실"})
    assert decide_receipt(sp, trip, law_snapshot).verdict is Verdict.REVIEW
    off = GOLD[0].model_copy(update={"origin": "부산", "destination": "대구"})
    assert decide_receipt(off, trip, law_snapshot).verdict is Verdict.REVIEW
    assert decide_receipt(GOLD[0], trip.model_copy(update={"grade": None}), law_snapshot).verdict is Verdict.REVIEW

def test_taxi_meal_and_errors(trip, law_snapshot):
    taxi = Receipt(receipt_id="t", image_id="t", category=Category.TAXI, amount=12000, service_date=date(2026, 7, 9))
    assert decide_receipt(taxi, trip, law_snapshot).verdict is Verdict.REVIEW
    assert decide_receipt(taxi, trip.model_copy(update={"taxi_reason": "심야 대중교통 종료"}), law_snapshot).verdict is Verdict.PAY
    meal = Receipt(receipt_id="m", image_id="m", category=Category.MEAL, amount=9000, service_date=date(2026, 7, 9))
    assert decide_receipt(meal, trip, law_snapshot).verdict is Verdict.DENIED
    bad = GOLD[0].model_copy(update={"warnings": ["DUP_APPROVAL"]})
    d = decide_receipt(bad, trip, law_snapshot)
    assert d.verdict is Verdict.REVIEW and "승인번호" in d.reasons[0]

def test_within_workplace_and_official_vehicle(trip, law_snapshot):
    t = trip.model_copy(update={"within_workplace": True, "duration_hours": 5})
    rows = allowance_rows(t, law_snapshot)
    assert [r.item for r in rows] == ["근무지 내 출장 여비"] and rows[0].approved_amount == 20000
    assert decide_receipt(GOLD[0], t, law_snapshot).verdict is Verdict.DENIED
    half = allowance_rows(trip.model_copy(update={"official_vehicle": True}), law_snapshot)
    assert {r.item: r.approved_amount for r in half} == {"일비": 25000, "식비": 50000}
    assert allowance_rows(trip.model_copy(update={"start_date": None}), law_snapshot)[0].verdict is Verdict.REVIEW

def test_registry_covers_receipt_categories():
    assert {Category.RAIL, Category.BUS, Category.AIR, Category.TAXI, Category.LODGING, Category.MEAL} <= set(RULES)

def test_cross_checks_lodging_overlap_nights_and_dup_ticket(trip, law_snapshot):
    s1 = Receipt(receipt_id="s1", image_id="s1", category=Category.LODGING, amount=90000, service_date=date(2026, 7, 9), nights=1, region="서울")
    s2 = s1.model_copy(update={"receipt_id": "s2", "image_id": "s2"})
    k2 = GOLD[0].model_copy(update={"receipt_id": "k2", "image_id": "k2", "approval_no": "1"})
    out = {r.receipt_id: r for r in apply_cross_checks([s1, s2, GOLD[0], k2, GOLD[1]], trip)}
    assert "LODGING_OVERLAP" in out["s1"].warnings and "LODGING_NIGHTS_EXCEED" in out["s2"].warnings
    assert "DUP_TICKET" in out["ktx1"].warnings and "DUP_TICKET" in out["k2"].warnings and out["ktx2"].warnings == []
    d = decide_receipt(out["s1"], trip, law_snapshot)
    assert d.verdict is Verdict.REVIEW and "겹침" in d.reasons[0]

def test_cross_trip_duplicates():
    a = GOLD[0].model_copy(update={"sha256": "same"})
    b = GOLD[1].model_copy(update={"sha256": "other"})
    c = GOLD[0].model_copy(update={"sha256": "same", "receipt_id": "x"})
    out = mark_cross_trip_duplicates({"t1": [a, b], "t2": [c]})
    assert "DUP_ACROSS_TRIPS" in out["t1"][0].warnings and out["t1"][1].warnings == [] and "DUP_ACROSS_TRIPS" in out["t2"][0].warnings

def test_proposed_trip_allowances_need_review(trip, law_snapshot):
    rows = allowance_rows(trip.model_copy(update={"proposed": True}), law_snapshot)
    assert [r.item for r in rows] == ["일비", "식비"]
    assert all(r.verdict is Verdict.REVIEW and r.approved_amount == 0 for r in rows) and "50,000" in rows[0].reasons[0]

# tests/test_rules.py
import json, unicodedata
from datetime import date
from pathlib import Path
from receipt_evidence.models import LawParams, Receipt, Category, Verdict
from receipt_evidence.rules import (RULES, allowance_rows, apply_cross_checks, decide_all, decide_receipt, mark_cross_trip_duplicates,
                                    region_key, review_items, totals)

GOLD = [Receipt(**d) for d in json.loads((Path(__file__).parent / "fixtures/golden/receipts.json").read_text(encoding="utf-8"))]

def test_region_key():
    assert region_key("서울 강남구") == "서울특별시" and region_key("부산광역시") == "광역시" and region_key("광주") == "광역시"
    assert region_key("전남 나주시") == "그 밖의 지역" and region_key("경기도 수원") == "그 밖의 지역"
    assert region_key(unicodedata.normalize("NFD", "서울 강남구")) == "서울특별시"  # NFD 입력도 같은 상한

def test_region_key_unified_special_city_annex2_note5():
    # 별표2 비고 5: 통합특별시는 종전 전라남도 시·군(그 밖의 지역)과 광주광역시(광역시)를 기준으로 한다
    assert region_key("전남광주통합특별시 목포시") == "그 밖의 지역" and region_key("통합특별시 여수") == "그 밖의 지역"
    assert region_key("전남광주통합특별시 광산구") == "광역시" and region_key("통합특별시 (종전 광주광역시) 동구") == "광역시"
    assert region_key("전남광주통합특별시") is None and region_key("광주전남 통합특별시") is None  # 종전 구역을 알 수 없음
    assert region_key("광주시") is None  # 경기도 광주시와 헷갈리는 표기는 정하지 않는다
    assert region_key("경기도 광주시") == "그 밖의 지역" and region_key("세종특별자치시") == "그 밖의 지역"

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


def _stay(**kw):
    base = dict(receipt_id="s", image_id="s", category=Category.LODGING, amount=90000, service_date=date(2026, 7, 9), region="서울")
    return Receipt(**(base | kw))

def test_unified_city_lodging_needs_review_until_resolved(trip, law_snapshot):
    t = trip.model_copy(update={"end_date": date(2026, 7, 10)})
    d = decide_receipt(_stay(region="전남광주통합특별시", amount=75000), t, law_snapshot)
    assert d.verdict is Verdict.REVIEW and "별표2 비고 5" in d.basis and "시·군·구" in d.reasons[0]
    d = decide_receipt(_stay(region="전남광주통합특별시", amount=75000), t.model_copy(update={"lodging_region": "목포시"}), law_snapshot)
    assert d.verdict is Verdict.REDUCED and d.approved_amount == 70000 and "별표2 비고 5" in d.basis
    d = decide_receipt(_stay(region="전남광주통합특별시 광산구", amount=75000), t, law_snapshot)
    assert d.verdict is Verdict.PAY and d.approved_amount == 75000 and "광역시" in d.basis[0]

def test_lodging_nights_from_check_in_and_out(trip, law_snapshot):
    t = trip.model_copy(update={"end_date": date(2026, 7, 11)})
    two = _stay(amount=190000, service_end_date=date(2026, 7, 11))
    d = decide_receipt(two, t, law_snapshot)
    assert d.verdict is Verdict.PAY and d.approved_amount == 190000 and "2박" in d.reasons[0]  # 상한 100,000×2박
    out = apply_cross_checks([two], trip)  # 출장 2일(1박)인데 2박 영수증
    assert "LODGING_NIGHTS_EXCEED" in out[0].warnings

def test_over_cap_uses_article_ratio_and_deadline_note(trip, law_snapshot):
    t = trip.model_copy(update={"over_cap_reason": "행사장 인근 만실"})
    d = decide_receipt(_stay(amount=150000), t, law_snapshot)
    assert d.approved_amount == 130000 and any("2026. 7. 17.까지" in r and "제16조제2항" in r for r in d.reasons)
    other = law_snapshot.model_copy(update={"params": law_snapshot.params.model_copy(update={"over_cap_ratio": (5, 10)})})
    assert decide_receipt(_stay(amount=160000), t, other).approved_amount == 150000
    unknown = law_snapshot.model_copy(update={"params": law_snapshot.params.model_copy(update={"over_cap_ratio": None})})
    d = decide_receipt(_stay(amount=150000), t, unknown)
    assert d.verdict is Verdict.REVIEW and d.approved_amount == 0 and "제16조" in d.reasons[0]  # 확인필요는 인정액에 넣지 않는다

def test_in_city_and_vehicle_amounts_come_from_articles(trip, law_snapshot):
    t = trip.model_copy(update={"within_workplace": True, "duration_hours": 3, "official_vehicle": True})
    changed = law_snapshot.model_copy(update={"params": LawParams(in_city_hours=4, in_city_long=30000, in_city_short=15000, in_city_vehicle_cut=5000,
                                                                  over_cap_ratio=(3, 10), vehicle_daily_ratio=(1, 2))})
    assert allowance_rows(t, changed)[0].approved_amount == 10000
    blank = law_snapshot.model_copy(update={"params": LawParams()})
    row = allowance_rows(t, blank)[0]
    assert row.verdict is Verdict.REVIEW and "제18조" in row.reasons[0]
    half = allowance_rows(trip.model_copy(update={"official_vehicle": True}), blank)
    assert half[0].item == "일비" and half[0].verdict is Verdict.REVIEW and half[1].verdict is Verdict.PAY

from receipt_evidence.rules import apply_manual_decisions

def test_manual_decisions_override_rule_and_keep_trace(trip, law_snapshot):
    over = _stay(amount=150000)  # 서울 상한 100,000 초과, 사유 없음 → 규정상 감액지급 100,000
    meal = Receipt(receipt_id="m", image_id="m", category=Category.MEAL, amount=9000, service_date=date(2026, 7, 9))
    ds = decide_all([GOLD[2], over, meal, GOLD[0]], trip, law_snapshot)
    manual = {"stay": {"decision": {"verdict": "지급", "reason": "체크인 7/9 확인(결제 메일에 날짜 없음)"}},
              "s": {"decision": {"verdict": "지급", "reason": "기관장 사전 승인"}},
              "m": {"decision": {"verdict": "지급", "reason": "워크숍 식사 예외 승인"}},
              "ktx1": {"decision": {"verdict": "감액지급", "approved_amount": 40000, "reason": "일반실 차액만"}}}
    out = {d.receipt_id or d.item: d for d in apply_manual_decisions(ds, manual)}
    stay = out["stay"]
    assert stay.verdict is Verdict.PAY and stay.approved_amount == 100000 and stay.basis[0] == "담당자 판정"
    assert stay.manual.rule_verdict is Verdict.REVIEW and stay.manual.rule_approved == 0 and not stay.manual.over_rule
    assert "규정상 확인필요 0원" in stay.reasons[0] and "체크인 7/9" in stay.reasons[0]
    assert out["s"].approved_amount == 150000 and out["s"].manual.over_rule and out["s"].manual.rule_approved == 100000
    assert out["m"].verdict is Verdict.PAY and out["m"].manual.over_rule and out["m"].manual.rule_verdict is Verdict.DENIED
    assert out["ktx1"].verdict is Verdict.REDUCED and out["ktx1"].approved_amount == 40000 and not out["ktx1"].manual.over_rule
    assert out["일비"].manual is None and totals(list(out.values()))["review"] == 0

def test_invalid_manual_decisions_keep_rule(trip, law_snapshot):
    ds = decide_all([GOLD[0], GOLD[1], GOLD[2]], trip, law_snapshot)
    bad = {"ktx1": {"decision": {"verdict": "감액지급", "approved_amount": 60000, "reason": "청구액보다 큼"}},
           "ktx2": {"decision": {"verdict": "지급"}},
           "stay": {"decision": {"verdict": "마음대로", "reason": "x"}},
           "일비": {"decision": {"verdict": "불인정", "reason": "정액 행은 대상 아님"}}}
    out = {d.receipt_id or d.item: d for d in apply_manual_decisions(ds, bad)}
    for rid in ("ktx1", "ktx2", "stay"):
        assert out[rid].manual is None and "담당자 판정 형식 오류" in out[rid].reasons[-1]
    assert out["ktx1"].verdict is Verdict.PAY and out["stay"].verdict is Verdict.REVIEW and out["일비"].manual is None

def _allow(rows):
    return {r.item: r for r in rows}

def test_allowances_for_proposed_trip_depend_on_period_evidence(trip, law_snapshot):
    sure = _allow(allowance_rows(trip.model_copy(update={"proposed": True, "period_reliable": True}), law_snapshot))
    assert sure["일비"].verdict is Verdict.PAY and sure["일비"].approved_amount == 50000 and sure["식비"].approved_amount == 50000
    assert "25,000×2일" == sure["식비"].reasons[0] and "자동 제안 기간" in sure["식비"].reasons[1]
    unsure = _allow(allowance_rows(trip.model_copy(update={"proposed": True}), law_snapshot))
    assert unsure["일비"].verdict is Verdict.REVIEW and unsure["일비"].approved_amount == 0 and "50,000 예정" in unsure["일비"].reasons[0]
    blank = law_snapshot.model_copy(update={"params": LawParams()})
    both = _allow(allowance_rows(trip.model_copy(update={"proposed": True, "official_vehicle": True}), blank))
    assert both["식비"].verdict is Verdict.REVIEW  # 공용차량 비율을 못 읽어도 확정 전 식비는 지급하지 않는다

def test_allowances_reject_reversed_period(trip, law_snapshot):
    rows = _allow(allowance_rows(trip.model_copy(update={"end_date": date(2026, 7, 8)}), law_snapshot))
    assert rows["일비"].verdict is Verdict.REVIEW and rows["일비"].approved_amount == 0 and "종료일" in rows["일비"].reasons[0]

def test_allowance_manual_decisions_by_days_or_amount(trip, law_snapshot):
    t = trip.model_copy(update={"allowance_decisions": {"식비": {"days": 1, "reason": "둘째 날 교육기관 식사 제공"},
                                                        "일비": {"amount": 60000, "reason": "기관장 승인"}}})
    rows = _allow(allowance_rows(t, law_snapshot))
    meal, daily = rows["식비"], rows["일비"]
    assert meal.verdict is Verdict.REDUCED and meal.approved_amount == 25000 and meal.manual.days == 1 and not meal.manual.over_rule
    assert meal.basis[0] == "담당자 판정" and "25,000×1일" in meal.reasons[0] and "규정상 지급 50,000원" in meal.reasons[0]
    assert daily.verdict is Verdict.PAY and daily.approved_amount == 60000 and daily.manual.over_rule
    zero = _allow(allowance_rows(trip.model_copy(update={"allowance_decisions": {"식비": {"amount": 0, "reason": "숙식 전액 제공"}}}), law_snapshot))
    assert zero["식비"].verdict is Verdict.DENIED and zero["식비"].approved_amount == 0
    bad = _allow(allowance_rows(trip.model_copy(update={"allowance_decisions": {"식비": {"days": 1}, "일비": {"days": -1, "reason": "x"}}}), law_snapshot))
    assert bad["식비"].manual is None and "담당자 판정 형식 오류" in bad["식비"].reasons[-1] and bad["일비"].manual is None
    proposed = _allow(allowance_rows(trip.model_copy(update={"proposed": True, "allowance_decisions": {"일비": {"days": 2, "reason": "기간 확인"}}}), law_snapshot))
    assert proposed["일비"].verdict is Verdict.PAY and proposed["일비"].approved_amount == 50000 and not proposed["일비"].manual.over_rule

def test_in_city_allowance_manual_amount_only(trip, law_snapshot):
    t = trip.model_copy(update={"within_workplace": True, "duration_hours": 5, "allowance_decisions": {"근무지 내 출장 여비": {"amount": 10000, "reason": "오전만 출장"}}})
    row = allowance_rows(t, law_snapshot)[0]
    assert row.verdict is Verdict.REDUCED and row.approved_amount == 10000 and row.manual.rule_approved == 20000
    t2 = t.model_copy(update={"allowance_decisions": {"근무지 내 출장 여비": {"days": 1, "reason": "x"}}})
    assert "담당자 판정 형식 오류" in allowance_rows(t2, law_snapshot)[0].reasons[-1]

def test_air_only_trip_meal_note(trip, law_snapshot):
    air = Receipt(receipt_id="a", image_id="a", category=Category.AIR, amount=90000, service_date=date(2026, 7, 9), origin="김포", destination="제주")
    meal = _allow(decide_all([air], trip, law_snapshot))["식비"]
    assert meal.verdict is Verdict.PAY and any("제16조제5항 단서" in r for r in meal.reasons)
    assert not any("단서" in r for r in _allow(decide_all([GOLD[0]], trip, law_snapshot))["식비"].reasons)

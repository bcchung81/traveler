# src/receipt_evidence/rules.py
from __future__ import annotations
from collections.abc import Callable
from datetime import date, timedelta
from .models import Category, Decision, LawSnapshot, RateTable, Receipt, TripConfig, Verdict
from .validate import ERROR_CODES

RULES_VERSION = "r1"  # 판정 로직을 바꾸면 올린다 → fingerprint가 달라져 새 버전 문서가 생성됨
CROSS_CODES = frozenset({"LODGING_OVERLAP", "LODGING_NIGHTS_EXCEED", "DUP_TICKET", "DUP_ACROSS_TRIPS"})
WARNING_TEXT = {
    "MISSING_AMOUNT": "금액을 읽지 못함", "AMOUNT_NOT_IN_TRANSCRIPT": "금액이 영수증 원문에서 확인되지 않음",
    "BIZNO_CHECKSUM": "사업자등록번호 검증 실패", "DUP_APPROVAL": "승인번호가 다른 영수증과 같음",
    "EXTRACT_FAILED": "영수증을 읽지 못함", "MISSING_DATE": "날짜를 읽지 못함",
    "LODGING_OVERLAP": "다른 숙박 영수증과 날짜가 겹침", "LODGING_NIGHTS_EXCEED": "숙박 박수 합계가 출장 박수보다 많음",
    "DUP_TICKET": "같은 날짜·편명·구간의 승차권이 중복", "DUP_ACROSS_TRIPS": "같은 영수증 파일이 다른 출장에도 있음",
}
_METRO = ("부산", "대구", "인천", "대전", "울산", "광주")
ITEM = {Category.RAIL: "철도운임", Category.BUS: "버스운임", Category.AIR: "항공운임", Category.TAXI: "자동차운임(택시)",
        Category.LODGING: "숙박비", Category.MEAL: "식비(영수증)", Category.OTHER: "기타", Category.UNKNOWN: "미상"}
Handler = Callable[[Receipt, TripConfig, RateTable, str, int], Decision]

def region_key(region: str) -> str:
    r = region.replace(" ", "")
    if "서울" in r:
        return "서울특별시"
    if "광역시" in r or any(r.startswith(m) for m in _METRO):
        return "광역시"
    return "그 밖의 지역"

def _d(r: Receipt, item: str, claimed: int, approved: int, v: Verdict, basis: list[str], reasons: list[str]) -> Decision:
    return Decision(receipt_id=r.receipt_id, item=item, claimed_amount=claimed, approved_amount=approved, verdict=v, basis=basis, reasons=reasons)

def _base_date(r: Receipt) -> date | None:
    return r.service_date or (r.paid_at.date() if r.paid_at else None)

def _rail(r: Receipt, trip: TripConfig, rt: RateTable, item: str, amt: int) -> Decision:
    if trip.route_stations and not any(s in (r.origin or "") or s in (r.destination or "") for s in trip.route_stations):
        return _d(r, item, amt, 0, Verdict.REVIEW, [], [f"구간 {r.origin}→{r.destination}이 출장 경로 {trip.route_stations}와 불일치"])
    if trip.grade == "제2호" and "특실" in (r.seat_class or ""):
        return _d(r, item, amt, 0, Verdict.REVIEW, ["별표2 제2호 철도운임 실비(일반실)"], ["특실 이용: 일반실 운임 차액 확인 필요"])
    return _d(r, item, amt, amt, Verdict.PAY, [f"별표2 {trip.grade} 철도운임 {rt.rail}", "별표2 비고 6"], [f"{r.train_no or '철도'} {r.origin}→{r.destination} 실비"])

def _bus(r: Receipt, trip: TripConfig, rt: RateTable, item: str, amt: int) -> Decision:
    return _d(r, item, amt, amt, Verdict.PAY, ["별표2 비고 3"], ["버스요금 실비"])

def _air(r: Receipt, trip: TripConfig, rt: RateTable, item: str, amt: int) -> Decision:
    return _d(r, item, amt, amt, Verdict.PAY, ["제12조", "별표2 항공운임 실비"], ["항공운임 실비"])

def _taxi(r: Receipt, trip: TripConfig, rt: RateTable, item: str, amt: int) -> Decision:
    if trip.taxi_reason:
        return _d(r, item, amt, amt, Verdict.PAY, ["제13조", "별표2 자동차운임 실비"], [f"부득이한 사유: {trip.taxi_reason}"])
    return _d(r, item, amt, 0, Verdict.REVIEW, ["제13조"], ["택시 이용의 부득이한 사유(taxi_reason) 미기재"])

def _lodging(r: Receipt, trip: TripConfig, rt: RateTable, item: str, amt: int) -> Decision:
    nights = r.nights or 1
    if trip.grade == "제1호":
        return _d(r, item, amt, amt, Verdict.PAY, ["별표2 제1호 숙박비 실비", "제16조제4항"], [f"{nights}박 실비"])
    region = r.region or trip.lodging_region
    if not region:
        return _d(r, item, amt, 0, Verdict.REVIEW, ["별표2 제2호 숙박비 상한"], ["숙박 지역 미확인(영수증에 숙소명·주소 없음). trip.yaml lodging_region 또는 overrides.yaml region 입력 필요"])
    key = region_key(region)
    cap = rt.lodging_caps[key] * nights
    basis = [f"별표2 제2호 숙박비 상한({key} {rt.lodging_caps[key]:,})", "제16조제4항"]
    if amt <= cap:
        return _d(r, item, amt, amt, Verdict.PAY, basis, [f"{nights}박, 상한 {cap:,} 이내"])
    if trip.over_cap_reason:
        limit = int(cap * 1.3)
        basis.append("제16조제1항 단서")
        if amt <= limit:
            return _d(r, item, amt, amt, Verdict.PAY, basis, [f"상한 초과분 30% 이내 추가지급, 사유: {trip.over_cap_reason}"])
        return _d(r, item, amt, limit, Verdict.REDUCED, basis, [f"상한의 130%({limit:,})로 감액, 사유: {trip.over_cap_reason}"])
    return _d(r, item, amt, cap, Verdict.REDUCED, basis, [f"상한 {cap:,} 초과분 불인정(부득이한 사유 없음)"])

def _meal(r: Receipt, trip: TripConfig, rt: RateTable, item: str, amt: int) -> Decision:
    return _d(r, item, amt, 0, Verdict.DENIED, ["제16조제5항", "별표2 식비"], ["식비는 여행일수 정액 지급, 영수증 실비 불인정"])

def _unknown(r: Receipt, trip: TripConfig, rt: RateTable, item: str, amt: int) -> Decision:
    return _d(r, item, amt, 0, Verdict.REVIEW, [], ["영수증 종류를 판별하지 못함"])

RULES: dict[Category, Handler] = {Category.RAIL: _rail, Category.BUS: _bus, Category.AIR: _air, Category.TAXI: _taxi,
                                  Category.LODGING: _lodging, Category.MEAL: _meal}

def decide_receipt(r: Receipt, trip: TripConfig, law: LawSnapshot) -> Decision:
    item, amt = ITEM[r.category], r.amount or 0
    blocking = [w for w in r.warnings if w in ERROR_CODES or w in CROSS_CODES]
    if r.amount is None or blocking:
        codes = blocking or ["MISSING_AMOUNT"]
        return _d(r, item, amt, 0, Verdict.REVIEW, [], ["검증 경고: " + ", ".join(WARNING_TEXT.get(c, c) for c in codes)])
    if trip.within_workplace and r.category in (Category.RAIL, Category.BUS, Category.AIR, Category.TAXI, Category.LODGING):
        return _d(r, item, amt, 0, Verdict.DENIED, ["제18조"], ["근무지 내 출장은 정액 지급 대상(운임·숙박비 별도 불인정)"])
    bd = _base_date(r)
    if trip.start_date and trip.end_date and bd and not (trip.start_date <= bd <= trip.end_date):
        return _d(r, item, amt, 0, Verdict.REVIEW, [], [f"기준일 {bd}이 출장기간 {trip.start_date}~{trip.end_date} 밖"])
    if trip.grade is None and r.category in (Category.RAIL, Category.LODGING):
        return _d(r, item, amt, 0, Verdict.REVIEW, ["별표1"], ["출장자 여비 지급 구분(제1호/제2호) 미확정 — traveler.yaml grade 입력 필요"])
    rt = law.rate_tables[trip.grade or "제2호"]
    return RULES.get(r.category, _unknown)(r, trip, rt, item, amt)

def allowance_rows(trip: TripConfig, law: LawSnapshot) -> list[Decision]:
    rt = law.rate_tables[trip.grade or "제2호"]
    mk = lambda item, appr, v, basis, reasons: Decision(receipt_id=None, item=item, claimed_amount=0, approved_amount=appr, verdict=v, basis=basis, reasons=reasons)
    if trip.within_workplace:
        if trip.duration_hours is None:
            return [mk("근무지 내 출장 여비", 0, Verdict.REVIEW, ["제18조"], ["출장 시간(duration_hours) 미입력"])]
        amt = (20000 if trip.duration_hours >= 4 else 10000) - (10000 if trip.official_vehicle else 0)
        return [mk("근무지 내 출장 여비", max(amt, 0), Verdict.PAY, ["제18조"], [f"{trip.duration_hours}시간, 공용차량 {'이용' if trip.official_vehicle else '미이용'}"])]
    days = trip.days
    if days is None:
        return [mk("일비", 0, Verdict.REVIEW, ["별표2", "제16조제3항"], ["출장기간 미입력"]), mk("식비", 0, Verdict.REVIEW, ["별표2", "제16조제5항"], ["출장기간 미입력"])]
    daily = rt.daily_allowance * days // (2 if trip.official_vehicle else 1)
    meal = rt.meal_allowance * days
    daily_note = f"{rt.daily_allowance:,}×{days}일" + (" ×1/2(공용차량)" if trip.official_vehicle else "")
    meal_note = f"{rt.meal_allowance:,}×{days}일"
    if trip.proposed:
        note = "출장기간이 자동 제안값 — trip.yaml로 확정 필요"
        return [mk("일비", 0, Verdict.REVIEW, ["별표2", "제16조제3항"], [f"{daily_note} = {daily:,} 예정", note]),
                mk("식비", 0, Verdict.REVIEW, ["별표2", "제16조제5항"], [f"{meal_note} = {meal:,} 예정", note])]
    return [mk("일비", daily, Verdict.PAY, ["별표2", "제16조제3항"], [daily_note]),
            mk("식비", meal, Verdict.PAY, ["별표2", "제16조제5항"], [meal_note])]

def decide_all(receipts: list[Receipt], trip: TripConfig, law: LawSnapshot) -> list[Decision]:
    return [decide_receipt(r, trip, law) for r in receipts] + allowance_rows(trip, law)

def _add_warnings(r: Receipt, codes: set[str]) -> Receipt:
    new = [c for c in sorted(codes) if c not in r.warnings]
    return r.model_copy(update={"warnings": r.warnings + new}) if new else r

def apply_cross_checks(receipts: list[Receipt], trip: TripConfig) -> list[Receipt]:
    add: dict[str, set[str]] = {r.receipt_id: set() for r in receipts}
    lodging = [r for r in receipts if r.category is Category.LODGING]
    dated = [r for r in lodging if r.service_date]
    span = lambda r: (r.service_date, r.service_end_date or (r.service_date + timedelta(days=r.nights or 1)))
    for i, a in enumerate(dated):
        a0, a1 = span(a)
        for b in dated[i + 1:]:
            b0, b1 = span(b)
            if a0 < b1 and b0 < a1:
                add[a.receipt_id].add("LODGING_OVERLAP")
                add[b.receipt_id].add("LODGING_OVERLAP")
    if trip.days is not None and lodging and sum(r.nights or 1 for r in lodging) > max(trip.days - 1, 0):
        for r in lodging:
            add[r.receipt_id].add("LODGING_NIGHTS_EXCEED")
    tickets: dict[tuple, list[str]] = {}
    for r in receipts:
        if r.category in (Category.RAIL, Category.BUS, Category.AIR) and r.service_date and r.train_no:
            tickets.setdefault((r.category, r.train_no, r.service_date, r.origin, r.destination), []).append(r.receipt_id)
    for ids in tickets.values():
        if len(ids) > 1:
            for rid in ids:
                add[rid].add("DUP_TICKET")
    return [_add_warnings(r, add[r.receipt_id]) for r in receipts]

def mark_cross_trip_duplicates(receipts_by_trip: dict[str, list[Receipt]]) -> dict[str, list[Receipt]]:
    owners: dict[str, set[str]] = {}
    for key, rs in receipts_by_trip.items():
        for r in rs:
            if r.sha256:
                owners.setdefault(r.sha256, set()).add(key)
    dup = {sha for sha, keys in owners.items() if len(keys) > 1}
    return {key: [_add_warnings(r, {"DUP_ACROSS_TRIPS"}) if r.sha256 in dup else r for r in rs] for key, rs in receipts_by_trip.items()}

def totals(decisions: list[Decision]) -> dict[str, int]:
    return {"claimed": sum(d.claimed_amount for d in decisions), "approved": sum(d.approved_amount for d in decisions),
            "review": sum(d.claimed_amount for d in decisions if d.verdict is Verdict.REVIEW)}

def review_items(decisions: list[Decision], receipts: list[Receipt]) -> list[str]:
    return [f"[{d.receipt_id or d.item}] {d.item}: " + "; ".join(d.reasons) for d in decisions if d.verdict is Verdict.REVIEW]

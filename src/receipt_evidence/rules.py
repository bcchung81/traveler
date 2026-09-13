# src/receipt_evidence/rules.py
from __future__ import annotations
import unicodedata
from collections.abc import Callable
from datetime import date, timedelta
from .models import Category, Decision, LawSnapshot, ManualDecision, RateTable, Receipt, TripConfig, Verdict
from .validate import ERROR_CODES

RULES_VERSION = "r2"  # 판정 로직을 바꾸면 올린다 → fingerprint가 달라져 새 버전 문서가 생성됨
CROSS_CODES = frozenset({"LODGING_OVERLAP", "LODGING_NIGHTS_EXCEED", "DUP_TICKET", "DUP_ACROSS_TRIPS"})
WARNING_TEXT = {
    "MISSING_AMOUNT": "금액을 읽지 못함", "AMOUNT_NOT_IN_TRANSCRIPT": "금액이 영수증 원문에서 확인되지 않음",
    "BIZNO_CHECKSUM": "사업자등록번호 검증 실패", "DUP_APPROVAL": "승인번호가 다른 영수증과 같음",
    "EXTRACT_FAILED": "영수증을 읽지 못함", "MISSING_DATE": "날짜를 읽지 못함",
    "LODGING_OVERLAP": "다른 숙박 영수증과 날짜가 겹침", "LODGING_NIGHTS_EXCEED": "숙박 박수 합계가 출장 박수보다 많음",
    "DUP_TICKET": "같은 날짜·편명·구간의 승차권이 중복", "DUP_ACROSS_TRIPS": "같은 영수증 파일이 다른 출장에도 있음",
}
_METRO = ("부산", "대구", "인천", "대전", "울산", "광주")
# 별표2 비고 5: 통합특별시 숙박비는 종전 전라남도 시·군과 광주광역시를 기준으로 한다
_JEONNAM = ("목포", "여수", "순천", "나주", "광양", "담양", "곡성", "구례", "고흥", "보성", "화순", "장흥", "강진", "해남", "영암", "무안",
            "함평", "영광", "장성", "완도", "진도", "신안")
_GWANGJU_GU = ("광주광역시", "광산구", "동구", "서구", "남구", "북구")
ITEM = {Category.RAIL: "철도운임", Category.BUS: "버스운임", Category.AIR: "항공운임", Category.TAXI: "자동차운임(택시)",
        Category.LODGING: "숙박비", Category.MEAL: "식비(영수증)", Category.OTHER: "기타", Category.UNKNOWN: "미상"}
Handler = Callable[[Receipt, TripConfig, LawSnapshot, RateTable, str, int], Decision]

def _flat_region(region: str) -> str:
    return unicodedata.normalize("NFC", region).replace(" ", "")  # NFD 입력(macOS 파일명 유래)도 같은 상한으로

def is_unified_city(region: str) -> bool:
    r = _flat_region(region)
    return "통합특별시" in r or (("전남" in r or "전라남도" in r) and "광주" in r)

def region_key(region: str) -> str | None:
    """숙박비 상한 구분. 정할 수 없으면 None(통합특별시인데 종전 구역을 모름, '광주시'처럼 모호한 표기)."""
    r = _flat_region(region)
    if "서울" in r:
        return "서울특별시"
    if is_unified_city(r):
        if any(n in r for n in _JEONNAM):
            return "그 밖의 지역"
        return "광역시" if any(g in r for g in _GWANGJU_GU) else None
    if "광역시" in r:
        return "광역시"
    if r.startswith("광주시"):
        return None
    if any(r.startswith(m) for m in _METRO):
        return "광역시"
    return "그 밖의 지역"

def stay_nights(r: Receipt) -> int:
    """숙박 밤 수: 영수증의 박수, 없으면 체크인·체크아웃 날짜 차이, 둘 다 없으면 1박."""
    if r.nights:
        return r.nights
    if r.service_date and r.service_end_date and r.service_end_date > r.service_date:
        return (r.service_end_date - r.service_date).days
    return 1

def _ratio_text(ratio: tuple[int, int] | None) -> str:
    return f"{ratio[1]}분의 {ratio[0]}" if ratio else "(조문 확인 필요)"

def _won_text(v: int | None) -> str:
    if v is None:
        return "(조문 확인 필요)"
    return f"{v // 10000}만원" if v % 10000 == 0 else f"{v:,}원"

def _kdate(d: date) -> str:
    return f"{d.year}. {d.month}. {d.day}."

def _d(r: Receipt, item: str, claimed: int, approved: int, v: Verdict, basis: list[str], reasons: list[str]) -> Decision:
    return Decision(receipt_id=r.receipt_id, item=item, claimed_amount=claimed, approved_amount=approved, verdict=v, basis=basis, reasons=reasons)

def _base_date(r: Receipt) -> date | None:
    return r.service_date or (r.paid_at.date() if r.paid_at else None)

def _rail(r: Receipt, trip: TripConfig, law: LawSnapshot, rt: RateTable, item: str, amt: int) -> Decision:
    if trip.route_stations and not any(s in (r.origin or "") or s in (r.destination or "") for s in trip.route_stations):
        return _d(r, item, amt, 0, Verdict.REVIEW, [], [f"구간 {r.origin}→{r.destination}이 출장 경로 {trip.route_stations}와 불일치"])
    if trip.grade == "제2호" and "특실" in (r.seat_class or ""):
        return _d(r, item, amt, 0, Verdict.REVIEW, ["별표2 제2호 철도운임 실비(일반실)"], ["특실 이용: 일반실 운임 차액 확인 필요"])
    return _d(r, item, amt, amt, Verdict.PAY, [f"별표2 {trip.grade} 철도운임 {rt.rail}", "별표2 비고 6"], [f"{r.train_no or '철도'} {r.origin}→{r.destination} 실비"])

def _bus(r: Receipt, trip: TripConfig, law: LawSnapshot, rt: RateTable, item: str, amt: int) -> Decision:
    return _d(r, item, amt, amt, Verdict.PAY, ["별표2 비고 3"], ["버스요금 실비"])

def _air(r: Receipt, trip: TripConfig, law: LawSnapshot, rt: RateTable, item: str, amt: int) -> Decision:
    return _d(r, item, amt, amt, Verdict.PAY, ["제12조", "별표2 항공운임 실비"], ["항공운임 실비"])

def _taxi(r: Receipt, trip: TripConfig, law: LawSnapshot, rt: RateTable, item: str, amt: int) -> Decision:
    if trip.taxi_reason:
        return _d(r, item, amt, amt, Verdict.PAY, ["제13조", "별표2 자동차운임 실비"], [f"부득이한 사유: {trip.taxi_reason}"])
    return _d(r, item, amt, 0, Verdict.REVIEW, ["제13조"], ["택시 이용의 부득이한 사유(taxi_reason) 미기재"])

def _lodging(r: Receipt, trip: TripConfig, law: LawSnapshot, rt: RateTable, item: str, amt: int) -> Decision:
    nights = stay_nights(r)
    if trip.grade == "제1호":
        return _d(r, item, amt, amt, Verdict.PAY, ["별표2 제1호 숙박비 실비", "제16조제4항"], [f"{nights}박 실비"])
    candidates = [x for x in (r.region, trip.lodging_region) if x]
    used = next((x for x in candidates if region_key(x)), None)
    if used is None:
        if candidates:
            return _d(r, item, amt, 0, Verdict.REVIEW, ["별표2 제2호 숙박비 상한", "별표2 비고 5"],
                      [f"숙박 지역 '{candidates[0]}'의 상한 구분을 정하지 못함(통합특별시는 종전 광주광역시 자치구·전남 시·군 기준). 숙박지 시·군·구 입력 필요"])
        return _d(r, item, amt, 0, Verdict.REVIEW, ["별표2 제2호 숙박비 상한"], ["숙박 지역 미확인(영수증에 숙소명·주소 없음). trip.yaml lodging_region 또는 overrides.yaml region 입력 필요"])
    key = region_key(used)
    cap = rt.lodging_caps[key] * nights
    basis = [f"별표2 제2호 숙박비 상한({key} {rt.lodging_caps[key]:,})", "제16조제4항"]
    if any(is_unified_city(x) for x in candidates):
        basis.append("별표2 비고 5")
    if amt <= cap:
        return _d(r, item, amt, amt, Verdict.PAY, basis, [f"{nights}박, 상한 {cap:,} 이내"])
    if not trip.over_cap_reason:
        return _d(r, item, amt, cap, Verdict.REDUCED, basis, [f"상한 {cap:,} 초과분 불인정(부득이한 사유 없음)"])
    ratio = law.params.over_cap_ratio
    if ratio is None:
        return _d(r, item, amt, 0, Verdict.REVIEW, basis + ["제16조제1항 단서"], ["제16조제1항 추가지급 한도 문구를 읽지 못함(규정 개정 가능성) — 한도 확인 필요"])
    limit = cap + cap * ratio[0] // ratio[1]
    basis.append("제16조제1항 단서")
    notes = [f"사유: {trip.over_cap_reason}"]
    if trip.end_date:
        notes.append(f"제16조제2항: {_kdate(trip.end_date + timedelta(days=7))}까지 카드 매출전표(세부 사용내용)를 갖춰 정산 신청 필요")
    if amt <= limit:
        return _d(r, item, amt, amt, Verdict.PAY, basis, [f"상한 초과분 {_ratio_text(ratio)} 이내 추가지급"] + notes)
    return _d(r, item, amt, limit, Verdict.REDUCED, basis, [f"상한의 {_ratio_text(ratio)} 추가 한도({limit:,})로 감액"] + notes)

def _meal(r: Receipt, trip: TripConfig, law: LawSnapshot, rt: RateTable, item: str, amt: int) -> Decision:
    return _d(r, item, amt, 0, Verdict.DENIED, ["제16조제5항", "별표2 식비"], ["식비는 여행일수 정액 지급, 영수증 실비 불인정"])

def _unknown(r: Receipt, trip: TripConfig, law: LawSnapshot, rt: RateTable, item: str, amt: int) -> Decision:
    return _d(r, item, amt, 0, Verdict.REVIEW, [], ["영수증 종류를 판별하지 못함"])

RULES: dict[Category, Handler] = {Category.RAIL: _rail, Category.BUS: _bus, Category.AIR: _air, Category.TAXI: _taxi,
                                  Category.LODGING: _lodging, Category.MEAL: _meal}

def decide_receipt(r: Receipt, trip: TripConfig, law: LawSnapshot) -> Decision:
    item, amt = ITEM[r.category], r.amount or 0
    blocking = [w for w in r.warnings if w in ERROR_CODES or w in CROSS_CODES]
    if r.amount is None or blocking:
        codes = blocking or ["MISSING_AMOUNT"]
        text = [WARNING_TEXT.get(c, c) + (f"(다른 출장: {', '.join(r.raw.get('dup_across_trips', []))})" if c == "DUP_ACROSS_TRIPS" and r.raw.get("dup_across_trips") else "")
                for c in codes]
        return _d(r, item, amt, 0, Verdict.REVIEW, [], ["검증 경고: " + ", ".join(text)])
    if trip.within_workplace and r.category in (Category.RAIL, Category.BUS, Category.AIR, Category.TAXI, Category.LODGING):
        return _d(r, item, amt, 0, Verdict.DENIED, ["제18조"], ["근무지 내 출장은 정액 지급 대상(운임·숙박비 별도 불인정)"])
    bd = _base_date(r)
    if trip.start_date and trip.end_date and bd and not (trip.start_date <= bd <= trip.end_date):
        return _d(r, item, amt, 0, Verdict.REVIEW, [], [f"기준일 {bd}이 출장기간 {trip.start_date}~{trip.end_date} 밖"])
    if trip.grade is None and r.category in (Category.RAIL, Category.LODGING):
        return _d(r, item, amt, 0, Verdict.REVIEW, ["별표1"], ["출장자 여비 지급 구분(제1호/제2호) 미확정 — traveler.yaml grade 입력 필요"])
    rt = law.rate_tables[trip.grade or "제2호"]
    return RULES.get(r.category, _unknown)(r, trip, law, rt, item, amt)

def allowance_rows(trip: TripConfig, law: LawSnapshot) -> list[Decision]:
    rt = law.rate_tables[trip.grade or "제2호"]
    mk = lambda item, appr, v, basis, reasons: Decision(receipt_id=None, item=item, claimed_amount=0, approved_amount=appr, verdict=v, basis=basis, reasons=reasons)
    p = law.params
    if trip.within_workplace:
        if trip.duration_hours is None:
            return [mk("근무지 내 출장 여비", 0, Verdict.REVIEW, ["제18조"], ["출장 시간(duration_hours) 미입력"])]
        needed = (p.in_city_hours, p.in_city_long, p.in_city_short) + ((p.in_city_vehicle_cut,) if trip.official_vehicle else ())
        if any(v is None for v in needed):
            return [mk("근무지 내 출장 여비", 0, Verdict.REVIEW, ["제18조"], ["제18조 금액 문구를 읽지 못함(규정 개정 가능성) — 지급액 확인 필요"])]
        amt = (p.in_city_long if trip.duration_hours >= p.in_city_hours else p.in_city_short) - (p.in_city_vehicle_cut if trip.official_vehicle else 0)
        return [mk("근무지 내 출장 여비", max(amt, 0), Verdict.PAY, ["제18조"], [f"{trip.duration_hours}시간, 공용차량 {'이용' if trip.official_vehicle else '미이용'}"])]
    days = trip.days
    if days is None:
        return [mk("일비", 0, Verdict.REVIEW, ["별표2", "제16조제3항"], ["출장기간 미입력"]), mk("식비", 0, Verdict.REVIEW, ["별표2", "제16조제5항"], ["출장기간 미입력"])]
    ratio = p.vehicle_daily_ratio if trip.official_vehicle else (1, 1)
    meal = rt.meal_allowance * days
    meal_note = f"{rt.meal_allowance:,}×{days}일"
    if ratio is None:
        return [mk("일비", 0, Verdict.REVIEW, ["별표2", "제16조제3항"], ["제16조제3항 공용차량 일비 비율 문구를 읽지 못함 — 확인 필요"]),
                mk("식비", meal, Verdict.PAY, ["별표2", "제16조제5항"], [meal_note])]
    daily = rt.daily_allowance * days * ratio[0] // ratio[1]
    daily_note = f"{rt.daily_allowance:,}×{days}일" + (f" ×{ratio[0]}/{ratio[1]}(공용차량)" if trip.official_vehicle else "")
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
    span = lambda r: (r.service_date, r.service_date + timedelta(days=stay_nights(r)))
    for i, a in enumerate(dated):
        a0, a1 = span(a)
        for b in dated[i + 1:]:
            b0, b1 = span(b)
            if a0 < b1 and b0 < a1:
                add[a.receipt_id].add("LODGING_OVERLAP")
                add[b.receipt_id].add("LODGING_OVERLAP")
    if trip.days is not None and lodging and sum(stay_nights(r) for r in lodging) > max(trip.days - 1, 0):
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

def mark_cross_trip_duplicates(receipts_by_trip: dict[str, list[Receipt]], owners: dict[str, set[str]] | None = None) -> dict[str, list[Receipt]]:
    """같은 영수증 파일(sha256)이 두 출장 이상에 있으면 DUP_ACROSS_TRIPS. owners로 이번에 돌리지 않는 출장의 파일까지 본다."""
    all_owners: dict[str, set[str]] = {sha: set(keys) for sha, keys in (owners or {}).items()}
    for key, rs in receipts_by_trip.items():
        for r in rs:
            if r.sha256:
                all_owners.setdefault(r.sha256, set()).add(key)
    out: dict[str, list[Receipt]] = {}
    for key, rs in receipts_by_trip.items():
        marked = []
        for r in rs:
            others = sorted(all_owners.get(r.sha256, set()) - {key}) if r.sha256 else []
            if others:
                r = _add_warnings(r, {"DUP_ACROSS_TRIPS"})
                r = r.model_copy(update={"raw": r.raw | {"dup_across_trips": others}})
            marked.append(r)
        out[key] = marked
    return out

MANUAL_VERDICTS = {"지급": Verdict.PAY, "감액지급": Verdict.REDUCED, "불인정": Verdict.DENIED}

def _manual_error(d: Decision, why: str) -> Decision:
    return d.model_copy(update={"reasons": d.reasons + [f"담당자 판정 형식 오류({why}) — 규정 판정 유지"]})

def apply_manual_decisions(decisions: list[Decision], overrides: dict[str, dict]) -> list[Decision]:
    """overrides.yaml의 decision(담당자 판정)을 규정 판정 위에 적용한다. 영수증 행만 대상이고, 원래 판정은 manual에 남긴다.
    지급=청구액 전액, 감액지급=0<금액<청구액, 불인정=0. 사유가 없거나 형식이 틀리면 규정 판정을 유지한다."""
    out: list[Decision] = []
    for d in decisions:
        spec = (overrides.get(d.receipt_id) or {}).get("decision") if d.receipt_id else None
        if not spec:
            out.append(d)
            continue
        if not isinstance(spec, dict):
            out.append(_manual_error(d, "형식"))
            continue
        verdict = MANUAL_VERDICTS.get(str(spec.get("verdict", "")).strip())
        reason = str(spec.get("reason") or "").strip()
        if verdict is None:
            out.append(_manual_error(d, "판정은 지급·감액지급·불인정 중 하나"))
            continue
        if not reason:
            out.append(_manual_error(d, "사유 없음"))
            continue
        if verdict is Verdict.PAY:
            approved = d.claimed_amount
        elif verdict is Verdict.DENIED:
            approved = 0
        else:
            raw = spec.get("approved_amount")
            digits = "".join(ch for ch in str(raw) if ch.isdigit()) if raw is not None else ""
            approved = int(digits) if digits else -1
            if not 0 < approved < d.claimed_amount:
                out.append(_manual_error(d, f"감액 인정액은 0보다 크고 청구액 {d.claimed_amount:,}보다 작아야 함"))
                continue
        over = d.verdict is not Verdict.REVIEW and approved > d.approved_amount
        manual = ManualDecision(verdict=verdict, approved_amount=approved, reason=reason, rule_verdict=d.verdict,
                                rule_approved=d.approved_amount, over_rule=over)
        reasons = [f"담당자 판정: {reason} (규정상 {d.verdict.value} {d.approved_amount:,}원)"] + (["규정 한도를 넘는 인정"] if over else []) + d.reasons
        out.append(d.model_copy(update={"verdict": verdict, "approved_amount": approved, "basis": ["담당자 판정"] + d.basis,
                                         "reasons": reasons, "manual": manual}))
    return out

def totals(decisions: list[Decision]) -> dict[str, int]:
    return {"claimed": sum(d.claimed_amount for d in decisions), "approved": sum(d.approved_amount for d in decisions),
            "review": sum(d.claimed_amount for d in decisions if d.verdict is Verdict.REVIEW)}

def review_items(decisions: list[Decision], receipts: list[Receipt]) -> list[str]:
    return [f"[{d.receipt_id or d.item}] {d.item}: " + "; ".join(d.reasons) for d in decisions if d.verdict is Verdict.REVIEW]

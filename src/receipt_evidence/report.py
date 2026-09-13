# src/receipt_evidence/report.py
from __future__ import annotations
from collections import defaultdict
from datetime import date
from .models import Category, Decision, LawSnapshot, Receipt, TripConfig, Verdict
from .rules import _ratio_text, _won_text, totals

REPORT_VERSION = "d2"  # 보고서·HWPX 서식을 바꾸면 올린다 → fingerprint가 달라져 새 버전 문서가 생성됨
DETAIL_HEADERS = ["연번", "일자", "구분", "가맹점", "승인번호", "결제액", "인정액", "판정", "근거"]
_CAT_ORDER = {c: i for i, c in enumerate(Category)}

def fmt_won(n: int) -> str:
    return f"{n:,}"

def _cell(s: object) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")

def md_table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)

def _kdate(d: date | None) -> str:
    return f"{d.year}. {d.month}. {d.day}." if d else "미정"

def _short(d: date) -> str:
    return f"{d.month}.{d.day}."  # 상세표 일자 칸은 좁아서 연도·공백을 뺀다(연도는 출장 개요에 있음)

def _receipt_day(r: Receipt) -> date | None:
    return r.service_date or (r.paid_at.date() if r.paid_at else None)

def order_decisions(decisions: list[Decision], receipts: list[Receipt]) -> list[Decision]:
    by_id = {r.receipt_id: r for r in receipts}
    def key(d: Decision):
        r = by_id.get(d.receipt_id or "")
        if r is None:
            return (1, date.max, 99, "")  # 정액 행: 뒤에, 원래 순서 유지(stable sort)
        return (0, _receipt_day(r) or date.max, _CAT_ORDER[r.category], r.receipt_id)
    return sorted(decisions, key=key)

def build_markdown(trip: TripConfig, law: LawSnapshot, receipts: list[Receipt], decisions: list[Decision],
                   image_names: dict[str, list[str]], version: int | None = None, law_notes: list[str] | None = None) -> str:
    by_id = {r.receipt_id: r for r in receipts}
    ordered = order_decisions(decisions, receipts)
    t = totals(decisions)
    period = f"{_kdate(trip.start_date)}~{_kdate(trip.end_date)}"
    overview = [["출장자", f"{trip.traveler_name} {trip.position}".strip()], ["여비 구분", trip.grade or "미확정"],
                ["근무지 / 출장지", f"{trip.workplace_region or '-'} / {trip.destination_region or '-'}"],
                ["출장기간", f"{period} ({trip.days or '-'}일)"], ["출장목적", trip.purpose or "-"], ["영수증", f"{len(receipts)}건"]]
    if version is not None:
        overview.append(["문서 버전", f"v{version}"])
    md = ["# 출장여비 영수증 증빙내역서",
          f"> {period} {trip.destination_region or '국내'} 출장 영수증 증빙내역을 공무원 여비 규정에 따라 검토하여 여비를 정산하고자 함", ""]
    if trip.proposed:
        md += [f"※ 출장 정보는 영수증으로 자동 제안한 값입니다(근거: {'; '.join(trip.proposal_basis) or '없음'}). trip.yaml로 확정해 주세요.", ""]
    md += ["## 출장 개요", md_table(["항목", "내용"], overview), "", "## 지급대상 요약"]
    grp: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for d in ordered:
        grp[d.item][0] += d.claimed_amount; grp[d.item][1] += d.approved_amount
    rows = [[k, fmt_won(v[0]), fmt_won(v[1])] for k, v in grp.items()] + [["합계", fmt_won(t["claimed"]), fmt_won(t["approved"])]]
    manual = [d for d in ordered if d.manual]
    over = [d for d in manual if d.manual.over_rule]
    md += [md_table(["구분", "청구액", "인정액"], rows), "", f"※ 확인필요 항목 청구액 합계: {fmt_won(t['review'])}원 (인정액 미포함)"]
    if over:
        md.append(f"※ 규정 한도를 넘어 인정한 항목 {len(over)}건(담당자 판정 내역 참조)")
    md += ["", "## 영수증별 상세"]
    detail = []
    for i, d in enumerate(ordered, start=1):
        r = by_id.get(d.receipt_id or "")
        day = _receipt_day(r) if r else None
        detail.append([str(i), _short(day) if day else ("정액" if r is None else "미상"), d.item, (r.merchant or "-") if r else "-",
                       (r.approval_no or "-") if r else "-", fmt_won(d.claimed_amount), fmt_won(d.approved_amount), d.verdict.value, ", ".join(d.basis) or "-"])
    detail.append(["합계", "", "", "", "", fmt_won(t["claimed"]), fmt_won(t["approved"]), "", ""])
    md += [md_table(DETAIL_HEADERS, detail), "", "## 적용 규정",
           f"### 공무원 여비 규정(대통령령, {_kdate(law.promulgated)} 개정, {_kdate(law.effective)} 시행)"]
    for g, rt in law.rate_tables.items():
        caps = ", ".join(f"{k} {fmt_won(v)}" for k, v in rt.lodging_caps.items()) if rt.lodging_caps else "실비"
        md.append(f"- {g}: 철도 {rt.rail}, 일비 {fmt_won(rt.daily_allowance)}/일, 식비 {fmt_won(rt.meal_allowance)}/일, 숙박비 {caps}")
    p = law.params
    hours = f"{p.in_city_hours:g}시간" if p.in_city_hours is not None else "(조문 확인 필요)"
    md += [f"- 제16조: 숙박비는 숙박한 밤의 수, 일비·식비는 여행일수 기준. 상한 초과 시 부득이한 사유가 있으면 상한액의 {_ratio_text(p.over_cap_ratio)} 이내 추가지급 가능",
           f"- 제18조: 근무지 내 국내출장은 정액({hours} 이상 {_won_text(p.in_city_long)}, 미만 {_won_text(p.in_city_short)})",
           "- 별표2 비고 5: 통합특별시 숙박비는 종전 전라남도 시·군과 광주광역시를 기준으로 적용"]
    md += [f"※ {n}" for n in (law_notes or [])]
    md += ["", "## 확인필요 사항"]
    reviews = [d for d in ordered if d.verdict is Verdict.REVIEW]
    md += [f"- {d.item}({d.receipt_id or '정액'}): {'; '.join(d.reasons)}" for d in reviews] or ["- 없음"]
    if manual:
        md += ["", "## 담당자 판정 내역"]
        md += [f"- {d.item}({d.receipt_id}): 규정상 {d.manual.rule_verdict.value} {fmt_won(d.manual.rule_approved)} → {d.verdict.value} {fmt_won(d.approved_amount)}"
               f" · 사유: {d.manual.reason}" + (" [규정 한도 초과 인정]" if d.manual.over_rule else "") for d in manual]
    md += ["", "## 붙임"]
    for d in ordered:
        names = image_names.get(d.receipt_id or "", [])
        if names:
            md += [f"### {d.item} 영수증 ({d.receipt_id})"] + [f"![]({n})" for n in names] + [""]
    return "\n".join(md).rstrip() + "\n"

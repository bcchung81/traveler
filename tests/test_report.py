# tests/test_report.py
import json
from datetime import date
from pathlib import Path
from receipt_evidence.models import Receipt
from receipt_evidence.rules import decide_all
from receipt_evidence.report import build_markdown, md_table, fmt_won, DETAIL_HEADERS

GOLD = [Receipt(**d) for d in json.loads((Path(__file__).parent / "fixtures/golden/receipts.json").read_text(encoding="utf-8"))]

def test_md_table_escapes_pipe():
    assert md_table(["a", "b"], [["x|y", "1"]]) == "| a | b |\n| --- | --- |\n| x\\|y | 1 |"

def test_build_markdown_structure(trip, law_snapshot):
    ds = decide_all(GOLD, trip, law_snapshot)
    md = build_markdown(trip, law_snapshot, GOLD, ds, {"ktx1": ["ktx1.jpg"], "stay": ["stay-p1.jpg", "stay-p2.jpg"]})
    lines = md.splitlines()
    assert lines[0] == "# 출장여비 영수증 증빙내역서" and lines[1].startswith("> ") and lines[1].endswith("하고자 함")
    for h in ("## 출장 개요", "## 지급대상 요약", "## 영수증별 상세", "## 적용 규정", "## 확인필요 사항", "## 붙임"):
        assert h in md
    assert "| " + " | ".join(DETAIL_HEADERS) + " |" in md and "| 합계 |" in md and fmt_won(196400) in md
    assert "![](ktx1.jpg)" in md and "![](stay-p2.jpg)" in md and "287535" not in md and "2026. 7. 1. 시행" in md
    assert "53618190" not in md and "| 영수증 | 3건 |" in md and "자동 제안" not in md

def test_sorting_version_and_proposed_notice(trip, law_snapshot):
    late = GOLD[1].model_copy(update={"receipt_id": "late", "image_id": "late"})
    early = GOLD[0].model_copy(update={"receipt_id": "early", "image_id": "early"})
    t = trip.model_copy(update={"proposed": True, "proposal_basis": ["폴더명 날짜 2026-07-09"]})
    ds = decide_all([late, early], t, law_snapshot)
    md = build_markdown(t, law_snapshot, [late, early], ds, {}, version=2)
    detail = md[md.index("## 영수증별 상세"):]
    assert detail.index("| 7.9. |") < detail.index("| 7.10. |") < detail.index("| 정액 |")  # 상세표 일자는 짧게(연도는 개요에)
    assert "※ 출장 정보는 영수증으로 자동 제안한 값입니다(근거: 폴더명 날짜 2026-07-09)" in md and "| 문서 버전 | v2 |" in md

def test_manual_decisions_section_and_over_rule_notice(trip, law_snapshot):
    from receipt_evidence.rules import apply_manual_decisions
    meal = Receipt(receipt_id="m", image_id="m", category="식사", amount=9000, service_date=date(2026, 7, 9))
    ds = apply_manual_decisions(decide_all(GOLD + [meal], trip, law_snapshot),
                                {"stay": {"decision": {"verdict": "지급", "reason": "체크인 확인"}}, "m": {"decision": {"verdict": "지급", "reason": "예외 승인"}}})
    md = build_markdown(trip, law_snapshot, GOLD + [meal], ds, {})
    assert "## 담당자 판정 내역" in md and "- 숙박비(stay): 규정상 확인필요 0 → 지급 100,000 · 사유: 체크인 확인" in md
    assert "[규정 한도 초과 인정]" in md and "※ 규정 한도를 넘어 인정한 항목 1건(담당자 판정 내역 참조)" in md
    detail = md[md.index("## 영수증별 상세"):md.index("## 적용 규정")]
    assert "담당자 판정, " in detail
    plain = build_markdown(trip, law_snapshot, GOLD, decide_all(GOLD, trip, law_snapshot), {})
    assert "담당자 판정" not in plain  # 담당자 판정이 없으면 서류는 그대로

def test_manual_allowance_listed_in_decision_section(trip, law_snapshot):
    t = trip.model_copy(update={"allowance_decisions": {"식비": {"days": 1, "reason": "식사 제공"}}})
    md = build_markdown(t, law_snapshot, GOLD, decide_all(GOLD, t, law_snapshot), {})
    assert "- 식비(정액): 규정상 지급 50,000 → 감액지급 25,000 · 사유: 식사 제공" in md and "(None)" not in md

# tests/test_law.py
from datetime import date
import pytest
from receipt_evidence.law import parse_search, parse_annex2, html_table_rows, fetch_law_snapshot, get_law_snapshot, LawParseError, save_snapshot, load_snapshot
from receipt_evidence.mcp_client import FakeToolCaller

def test_parse_search_current(law_fixture_text):
    assert parse_search(law_fixture_text["search_law.txt"]) == ("009402", "287535", date(2026, 6, 30), date(2026, 7, 1))

def test_parse_search_requires_current():
    with pytest.raises(LawParseError):
        parse_search("1. 공무원 여비 규정 [연혁]\n   - 법령ID: 1\n   - MST: 2\n   - 공포일: 20200101 / 시행일: 20200101")

def test_html_rows_join_br(law_fixture_text):
    rows = html_table_rows(law_fixture_text["annex2.html"])
    assert rows[3][0] == "구분" and "숙박비" in rows[3][6]

def test_parse_annex2(law_fixture_text):
    rt = parse_annex2(law_fixture_text["annex2.html"])
    assert rt["제2호"].lodging_caps == {"서울특별시": 100000, "광역시": 80000, "그 밖의 지역": 70000}
    assert rt["제1호"].lodging_caps is None and rt["제2호"].daily_allowance == 25000 and rt["제1호"].rail == "실비(특실)"

def test_parse_annex2_fails_loudly():
    with pytest.raises(LawParseError):
        parse_annex2("<table><tr><td>구분</td><td>철도운임</td></tr><tr><td>제2호</td><td>실비</td></tr></table>")

def test_fetch_snapshot_with_fake(law_fixture_text, tmp_path):
    t = law_fixture_text
    caller = FakeToolCaller({"search_law": lambda a: t["search_law.txt"],
                             "get_annexes": lambda a: t["annex1.html"] if a["annexNo"] == "1" else t["annex2.html"],
                             "get_law_text": lambda a: t["jo" + a["jo"][1:-1] + ".txt"]})
    s = fetch_law_snapshot(caller)
    assert s.mst == "287535" and "제16조" in s.articles and set(s.rate_tables) == {"제1호", "제2호"}
    assert all(c[1].get("mst", "287535") == "287535" for c in caller.calls)
    save_snapshot(s, tmp_path / "law.json"); assert load_snapshot(tmp_path / "law.json") == s

def test_daily_law_cache(law_fixture_text, tmp_path):
    t = law_fixture_text
    caller = FakeToolCaller({"search_law": lambda a: t["search_law.txt"],
                             "get_annexes": lambda a: t["annex1.html"] if a["annexNo"] == "1" else t["annex2.html"],
                             "get_law_text": lambda a: t["jo" + a["jo"][1:-1] + ".txt"]})
    s1 = get_law_snapshot(caller, tmp_path, date(2026, 9, 13))
    n = len(caller.calls)
    assert get_law_snapshot(caller, tmp_path, date(2026, 9, 13)) == s1 and len(caller.calls) == n
    assert (tmp_path / "law" / "2026-09-13.json").exists()
    get_law_snapshot(caller, tmp_path, date(2026, 9, 13), refresh=True)
    assert len(caller.calls) == 2 * n

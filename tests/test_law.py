# tests/test_law.py
from datetime import date, datetime, timedelta
import pytest
from receipt_evidence.law import (parse_search, parse_annex2, html_table_rows, fetch_law_snapshot, get_law_book, load_law_book, parse_law_params,
                                  LawParseError, LawUnavailable, save_snapshot, load_snapshot, BASELINE_DIR)
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

def _caller(t, fail=False, mst=None):
    def search(a):
        if fail:
            raise RuntimeError("fetch failed: getaddrinfo ENOTFOUND www.law.go.kr")
        return t["search_law.txt"] if mst is None else t["search_law.txt"].replace("MST: 287535", f"MST: {mst}")
    return FakeToolCaller({"search_law": search, "get_annexes": lambda a: t["annex1.html"] if a["annexNo"] == "1" else t["annex2.html"],
                           "get_law_text": lambda a: t["jo" + a["jo"][1:-1] + ".txt"]})

class _Raising(FakeToolCaller):
    def call_many(self, calls):
        raise RuntimeError("MCP 서버 시작 실패(npx -y korean-law-mcp)")

def test_parse_law_params_from_articles(law_fixture_text):
    t = law_fixture_text
    p = parse_law_params({"제16조": t["jo16.txt"], "제18조": t["jo18.txt"]})
    assert (p.in_city_hours, p.in_city_long, p.in_city_short, p.in_city_vehicle_cut) == (4, 20000, 10000, 10000)
    assert p.over_cap_ratio == (3, 10) and p.vehicle_daily_ratio == (1, 2) and p.complete
    changed = parse_law_params({"제16조": t["jo16.txt"].replace("10분의 3", "100분의 50"), "제18조": t["jo18.txt"].replace("2만원을 지급하고", "2만5천원을 지급하고")})
    assert changed.over_cap_ratio == (50, 100) and changed.in_city_long is None and not changed.complete

def test_snapshot_params_filled_on_fetch_and_load(law_fixture_text, tmp_path):
    s = fetch_law_snapshot(_caller(law_fixture_text))
    assert s.params.in_city_long == 20000
    raw = s.model_dump(mode="json"); raw.pop("params")
    (tmp_path / "old.json").write_text(__import__("json").dumps(raw, ensure_ascii=False), encoding="utf-8")
    assert load_snapshot(tmp_path / "old.json").params.over_cap_ratio == (3, 10)  # params 없는 예전 캐시도 조문에서 채운다

def test_law_book_online_then_same_day_cache(law_fixture_text, tmp_path):
    caller = _caller(law_fixture_text)
    b1 = get_law_book(caller, tmp_path, date(2026, 9, 13))
    n = len(caller.calls)
    assert b1.online and b1.current.mst == "287535" and b1.notes() == [] and (tmp_path / "law" / "snapshots" / "287535.json").exists()
    b2 = get_law_book(caller, tmp_path, date(2026, 9, 13))
    assert len(caller.calls) == n and b2.current.mst == "287535" and b2.online
    assert load_law_book(tmp_path, date(2026, 9, 13)).current.mst == "287535" and load_law_book(tmp_path, date(2026, 9, 14)) is None
    get_law_book(caller, tmp_path, date(2026, 9, 13), refresh=True)
    assert len(caller.calls) == 2 * n

def test_law_book_offline_falls_back_to_latest_held(law_fixture_text, tmp_path):
    get_law_book(_caller(law_fixture_text), tmp_path, date(2026, 9, 13))
    now = datetime(2026, 9, 14, 9, 0)
    off = _caller(law_fixture_text, fail=True)
    book = get_law_book(off, tmp_path, date(2026, 9, 14), now=now)
    assert not book.online and book.current.mst == "287535" and "ENOTFOUND" in book.error
    assert any("인터넷" in x and "2026. 9. 13." in x for x in book.notes())
    tries = len(off.calls)
    get_law_book(off, tmp_path, date(2026, 9, 14), now=now + timedelta(minutes=5))
    assert len(off.calls) == tries  # 방금 실패했으면 10분 동안은 다시 조회하지 않는다
    assert load_law_book(tmp_path, date(2026, 9, 14), now=now + timedelta(minutes=5)) is not None
    assert load_law_book(tmp_path, date(2026, 9, 14), now=now + timedelta(minutes=11)) is None
    get_law_book(off, tmp_path, date(2026, 9, 14), now=now + timedelta(minutes=11))
    assert len(off.calls) > tries
    assert not get_law_book(_Raising({}), tmp_path, date(2026, 9, 15)).online  # MCP 서버를 못 띄워도 폴백

def test_law_book_uses_packaged_baseline_when_nothing_held(tmp_path):
    book = get_law_book(_Raising({}), tmp_path, date(2026, 9, 14))
    assert not book.online and book.current.mst == "287535" and book.current.params.complete
    assert any((BASELINE_DIR).glob("*.json"))
    with pytest.raises(LawUnavailable):
        get_law_book(_Raising({}), tmp_path / "x", date(2026, 9, 14), baseline_dir=tmp_path / "none")

def test_law_book_records_amendment_and_picks_trip_date_version(law_fixture_text, tmp_path):
    get_law_book(_caller(law_fixture_text), tmp_path, date(2026, 9, 13))
    newer = _caller(law_fixture_text, mst="299999")
    orig_search = newer.handlers["search_law"]
    newer.handlers["search_law"] = lambda a: orig_search(a).replace("시행일: 20260701", "시행일: 20270101").replace("공포일: 20260630", "공포일: 20261201")
    book = get_law_book(newer, tmp_path, date(2027, 1, 5))
    assert book.current.mst == "299999" and book.amendment_notice(date(2027, 1, 5)) and "2027. 1. 1." in book.amendment_notice(date(2027, 1, 5))
    assert book.amendment_notice(date(2027, 3, 1)) is None  # 30일 지나면 배너를 내린다
    snap, notes = book.for_date(date(2026, 8, 1))
    assert snap.mst == "287535" and notes == []
    snap, notes = book.for_date(date(2027, 1, 3))
    assert snap.mst == "299999" and notes == []
    snap, notes = book.for_date(date(2026, 6, 20))
    assert snap.mst == "299999" and any("2026. 6. 20." in x and "현행" in x for x in notes)
    assert book.for_date(None)[0].mst == "299999"

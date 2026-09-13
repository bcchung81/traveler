# src/receipt_evidence/law.py
from __future__ import annotations
import re
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from .mcp_client import ToolCaller
from .models import LawSnapshot, RateTable

LAW_NAME = "공무원 여비 규정"
ARTICLES = ["제12조", "제13조", "제16조", "제18조"]

class LawParseError(Exception):
    pass

def parse_search(text: str) -> tuple[str, str, date, date]:
    for block in re.split(r"\n(?=\s*\d+\.\s)", text):
        # "공무원 여비 규정 시행규칙" 같은 유사 법령 블록을 배제하려고 제목 줄을 정확히 매칭
        if re.search(rf"^\s*\d+\.\s+{re.escape(LAW_NAME)}\s+\[현행\]", block, flags=re.M):
            law_id = re.search(r"법령ID:\s*(\d+)", block)
            mst = re.search(r"MST:\s*(\d+)", block)
            dates = re.search(r"공포일:\s*(\d{8})\s*/\s*시행일:\s*(\d{8})", block)
            if law_id and mst and dates:
                p, e = dates.groups()
                return law_id.group(1), mst.group(1), date.fromisoformat(f"{p[:4]}-{p[4:6]}-{p[6:]}"), date.fromisoformat(f"{e[:4]}-{e[4:6]}-{e[6:]}")
    raise LawParseError("search_law 응답에서 [현행] 공무원 여비 규정의 MST/시행일을 찾지 못함")

class _Rows(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows: list[list[str]] = []; self._cell: list[str] | None = None
    def handle_starttag(self, tag, attrs):
        if tag == "tr": self.rows.append([])
        elif tag in ("td", "th"): self._cell = []
        elif tag == "br" and self._cell is not None: self._cell.append(" ")
    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self.rows[-1].append(re.sub(r"\s+", " ", "".join(self._cell)).strip()); self._cell = None
    def handle_data(self, data):
        if self._cell is not None: self._cell.append(data)

def html_table_rows(html: str) -> list[list[str]]:
    p = _Rows(); p.feed(html); return p.rows

_CAP = re.compile(r"(서울특별시|광역시|그 밖의 지역)[은는]?\s*([\d,]+)")

def _won(s: str) -> int:
    m = re.search(r"[\d,]+", s)
    if not m:
        raise LawParseError(f"금액 파싱 실패: {s!r}")
    return int(m.group(0).replace(",", ""))

def parse_annex2(html: str) -> dict[str, RateTable]:
    rows = html_table_rows(html)
    header = next((r for r in rows if r and r[0] == "구분"), None)
    if not header or len(header) != 8:
        raise LawParseError("별표 2 헤더(구분…식비 8열)를 찾지 못함")
    idx = {name: i for i, name in enumerate(header)}
    col = lambda key: next(i for n, i in idx.items() if key in n)
    out: dict[str, RateTable] = {}
    for r in rows:
        if len(r) == 8 and r[0] in ("제1호", "제2호"):
            lodging = r[col("숙박비")]
            caps = {k: int(v.replace(",", "")) for k, v in _CAP.findall(lodging)} if "상한액" in lodging else None
            if caps is not None and len(caps) != 3:
                raise LawParseError(f"숙박비 상한액 파싱 불완전: {lodging!r}")
            out[r[0]] = RateTable(grade=r[0], rail=r[col("철도")].replace(" ", ""), ship=r[col("선박")].replace(" ", ""), air=r[col("항공")],
                                  car=r[col("자동차")], daily_allowance=_won(r[col("일비")]), lodging=lodging.split("(")[0].strip(),
                                  lodging_caps=caps, meal_allowance=_won(r[col("식비")]))
    if set(out) != {"제1호", "제2호"}:
        raise LawParseError("별표 2에서 제1호/제2호 행을 모두 찾지 못함")
    return out

def fetch_law_snapshot(caller: ToolCaller) -> LawSnapshot:
    search = caller.call_many([("search_law", {"query": LAW_NAME, "display": 5})])[0]
    if search.is_error:
        raise LawParseError(search.text)
    law_id, mst, prom, eff = parse_search(search.text)
    calls = [("get_annexes", {"lawName": LAW_NAME, "annexNo": "1", "knd": "1"}), ("get_annexes", {"lawName": LAW_NAME, "annexNo": "2", "knd": "1"})]
    calls += [("get_law_text", {"mst": mst, "jo": jo}) for jo in ARTICLES]
    res = caller.call_many(calls)
    for (name, args), r in zip(calls, res):
        if r.is_error:
            raise LawParseError(f"{name} {args}: {r.text}")
    return LawSnapshot(law_name=LAW_NAME, law_id=law_id, mst=mst, promulgated=prom, effective=eff, fetched_at=datetime.now(),
                       annexes={"별표1": res[0].text, "별표2": res[1].text}, articles={jo: r.text for jo, r in zip(ARTICLES, res[2:])},
                       rate_tables=parse_annex2(res[1].text))

def save_snapshot(s: LawSnapshot, path: Path) -> None:
    path.write_text(s.model_dump_json(indent=2), encoding="utf-8")

def load_snapshot(path: Path) -> LawSnapshot:
    return LawSnapshot.model_validate_json(path.read_text(encoding="utf-8"))

def get_law_snapshot(caller: ToolCaller, cache_dir: Path, today: date, *, refresh: bool = False) -> LawSnapshot:
    """배치당 1회 조회. 같은 날짜 캐시가 있으면 재사용하고, refresh=True면 다시 조회한다."""
    path = cache_dir / "law" / f"{today.isoformat()}.json"
    if path.exists() and not refresh:
        return load_snapshot(path)
    snap = fetch_law_snapshot(caller)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_snapshot(snap, path)
    return snap

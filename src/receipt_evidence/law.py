# src/receipt_evidence/law.py
from __future__ import annotations
import json, logging, re, threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from .mcp_client import LAW_OC_URL, LawKeyMissing, ToolCaller, law_oc
from .models import LawParams, LawSnapshot, RateTable

LAW_NAME = "공무원 여비 규정"
ARTICLES = ["제12조", "제13조", "제16조", "제18조"]
BASELINE_DIR = Path(__file__).parent / "law_baseline"  # 저장소에 동봉한 기준 스냅샷 — 처음부터 인터넷이 없어도 판정할 수 있게
OFFLINE_RETRY = timedelta(minutes=10)                 # 조회에 실패하면 이 시간 동안은 저장해 둔 규정을 바로 쓴다
AMENDMENT_BANNER_DAYS = 30

log = logging.getLogger("receipt_evidence")

class LawParseError(Exception):
    pass

class LawUnavailable(RuntimeError):
    """조회도 실패했고 저장해 둔 규정도 없음."""

def kdate(d: date) -> str:
    return f"{d.year}. {d.month}. {d.day}."

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

_NUM = r"(\d+(?:\.\d+)?)"
_WON = r"(\d[\d,]*)\s*(만)?\s*원"

def _won_ko(num: str, man: str | None) -> int:
    v = int(num.replace(",", ""))
    return v * 10000 if man else v

def parse_law_params(articles: dict[str, str]) -> LawParams:
    """제16조·제18조 본문에서 판정에 쓰는 금액·비율을 읽는다. 문구가 바뀌어 맞지 않으면 그 값은 None."""
    a16, a18 = re.sub(r"\s+", " ", articles.get("제16조", "")), re.sub(r"\s+", " ", articles.get("제18조", ""))
    p: dict = {}
    m = re.search(rf"여행시간이 {_NUM} ?시간 이상인 공무원에게는 {_WON}을 지급하고, ?{_NUM} ?시간 미만인 공무원에게는 {_WON}을 지급", a18)
    if m and float(m.group(1)) == float(m.group(4)):
        p |= {"in_city_hours": float(m.group(1)), "in_city_long": _won_ko(m.group(2), m.group(3)), "in_city_short": _won_ko(m.group(5), m.group(6))}
    m = re.search(rf"공무용 차량을 이용하는 경우[^.]*?{_WON}을 감액하여 지급", a18)
    if m:
        p["in_city_vehicle_cut"] = _won_ko(m.group(1), m.group(2))
    m = re.search(r"국내 여행의 경우에는 숙박비 상한액의 (\d+) ?분의 (\d+)", a16)
    if m:
        p["over_cap_ratio"] = (int(m.group(2)), int(m.group(1)))
    m = re.search(r"일비의 (\d+) ?분의 (\d+)을 지급", a16)
    if m:
        p["vehicle_daily_ratio"] = (int(m.group(2)), int(m.group(1)))
    return LawParams(**p)

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
    articles = {jo: r.text for jo, r in zip(ARTICLES, res[2:])}
    return LawSnapshot(law_name=LAW_NAME, law_id=law_id, mst=mst, promulgated=prom, effective=eff, fetched_at=datetime.now(),
                       annexes={"별표1": res[0].text, "별표2": res[1].text}, articles=articles,
                       rate_tables=parse_annex2(res[1].text), params=parse_law_params(articles))

def save_snapshot(s: LawSnapshot, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_text(s.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)

def load_snapshot(path: Path) -> LawSnapshot:
    s = LawSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
    if s.articles:  # 예전 캐시(params 없음)도, 파서를 고친 뒤에도 조문에서 다시 읽는다
        s = s.model_copy(update={"params": parse_law_params(s.articles)})
    return s

# ---- 규정 보관소: law/snapshots/<MST>.json(받은 규정 전부) · law/status.json(오늘 조회 결과) · law/amendments.json(개정 기록) ----

def _law_dir(cache_dir: Path) -> Path:
    return Path(cache_dir) / "law"

def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default

def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def held_snapshots(cache_dir: Path, baseline_dir: Path = BASELINE_DIR) -> list[LawSnapshot]:
    """동봉 기준본 + 받아 둔 규정(예전 날짜별 캐시 포함). MST마다 가장 최근에 받은 것 하나, 시행일 순."""
    d = _law_dir(cache_dir)
    files = sorted(Path(baseline_dir).glob("*.json")) + sorted((d / "snapshots").glob("*.json")) + sorted(d.glob("????-??-??.json"))
    by_mst: dict[str, LawSnapshot] = {}
    for f in files:
        try:
            s = load_snapshot(f)
        except (OSError, ValueError):
            continue
        if s.mst not in by_mst or s.fetched_at > by_mst[s.mst].fetched_at:
            by_mst[s.mst] = s
    return sorted(by_mst.values(), key=lambda s: (s.effective, s.fetched_at))

@dataclass
class LawBook:
    """판정에 쓸 규정 묶음. current는 오늘 기준 현행(오프라인이면 가장 최근에 받아 둔 것)."""
    current: LawSnapshot
    snapshots: list[LawSnapshot]
    online: bool
    checked_at: datetime | None = None
    error: str | None = None
    amendments: list[dict] = field(default_factory=list)
    no_key: bool = False  # 법제처 인증키(LAW_OC)가 없어 조회하지 않음

    def notes(self) -> list[str]:
        """서류에 남는 안내(사실만)."""
        if self.online:
            return []
        why = "법제처 인증키가 설정되지 않아" if self.no_key else "인터넷에 연결되지 않아"
        return [f"{why} {kdate(self.current.fetched_at.date())}에 조회해 둔 공무원 여비 규정({kdate(self.current.effective)} 시행)을 적용함"]

    def for_date(self, d: date | None) -> tuple[LawSnapshot, list[str]]:
        """출장 시작일에 시행 중이던 규정. 받아 둔 것이 없으면 현행을 쓰고 안내만 붙인다(판정은 바꾸지 않음)."""
        notes = self.notes()
        if d is None or self.current.effective <= d:
            return self.current, notes
        older = [s for s in self.snapshots if s.effective <= d]
        if older:
            return max(older, key=lambda s: (s.effective, s.fetched_at)), notes
        return self.current, notes + [f"출장 시작일({kdate(d)})에 시행되던 규정의 금액표를 갖고 있지 않아 현행 규정({kdate(self.current.effective)} 시행)을 적용함"]

    def amendment_notice(self, today: date) -> str | None:
        if not self.amendments:
            return None
        a = self.amendments[-1]
        if (today - date.fromisoformat(a["detected_on"])).days > AMENDMENT_BANNER_DAYS:
            return None
        head = f"공무원 여비 규정이 개정됐어요({kdate(date.fromisoformat(a['effective']))} 시행)."
        if a.get("same_amounts"):
            return head + " 금액표와 조문 금액은 달라지지 않았어요."
        return head + " 금액이나 기준이 바뀌었을 수 있으니 판정 결과를 확인해 주세요."

    def notices(self, today: date) -> list[str]:
        """화면·요약에만 보이는 안내(할 일 포함)."""
        out = [x for x in (self.amendment_notice(today),) if x]
        if self.no_key:
            out.append(f"법제처 인증키(LAW_OC)가 없어 최신 여비 규정을 조회하지 않았어요. {LAW_OC_URL} 에서 무료로 발급받아 설정하면 "
                       "그날 현행 규정으로 판정해요 — 설정 방법은 README의 '법제처 인증키'를 보세요.")
        if not self.current.params.complete:
            out.append("규정 조문 문구가 바뀌어 일부 금액(근무지 내 출장·추가지급 한도 등)을 읽지 못했어요. 해당 항목은 확인필요로 둬요.")
        return out

def load_law_book(cache_dir: Path, today: date, *, now: datetime | None = None, baseline_dir: Path = BASELINE_DIR) -> LawBook | None:
    """네트워크 없이 읽기만 한다. 오늘 조회한 기록이 없거나, 조회 실패 후 재시도 시간이 지났으면 None(조회가 필요함)."""
    status = _read_json(_law_dir(cache_dir) / "status.json", None)
    if not status or status.get("checked_on") != today.isoformat():
        return None
    checked_at = datetime.fromisoformat(status["checked_at"])
    if not status["online"] and (now or datetime.now()) - checked_at >= OFFLINE_RETRY:
        return None
    if status.get("no_key") and law_oc():
        return None  # 그사이 인증키를 설정했으면 바로 다시 조회한다
    held = held_snapshots(cache_dir, baseline_dir)
    current = next((s for s in held if s.mst == status["mst"]), None)
    if current is None:
        return None
    return LawBook(current=current, snapshots=held, online=bool(status["online"]), checked_at=checked_at, error=status.get("error"),
                   amendments=_read_json(_law_dir(cache_dir) / "amendments.json", []), no_key=bool(status.get("no_key")))

def peek_law_book(cache_dir: Path, *, baseline_dir: Path = BASELINE_DIR) -> LawBook | None:
    """조회 신선도와 상관없이 저장된 것만으로 만든다(홈 화면 배너용)."""
    held = held_snapshots(cache_dir, baseline_dir)
    if not held:
        return None
    status = _read_json(_law_dir(cache_dir) / "status.json", {})
    current = next((s for s in held if s.mst == status.get("mst")), held[-1])
    return LawBook(current=current, snapshots=held, online=bool(status.get("online", True)), error=status.get("error"),
                   amendments=_read_json(_law_dir(cache_dir) / "amendments.json", []), no_key=bool(status.get("no_key")) and not law_oc())

def get_law_book(caller: ToolCaller, cache_dir: Path, today: date, *, refresh: bool = False, now: datetime | None = None,
                 baseline_dir: Path = BASELINE_DIR) -> LawBook:
    """현행 규정을 하루 한 번 조회한다. 조회에 실패하면(인터넷·MCP 서버 문제) 받아 둔 가장 최근 규정으로 계속한다."""
    now = now or datetime.now()
    if not refresh:
        book = load_law_book(cache_dir, today, now=now, baseline_dir=baseline_dir)
        if book is not None:
            return book
    d = _law_dir(cache_dir)
    held = held_snapshots(cache_dir, baseline_dir)
    status = {"checked_on": today.isoformat(), "checked_at": now.isoformat(timespec="seconds")}
    try:
        snap = fetch_law_snapshot(caller)
    except Exception as e:
        if not held:
            raise LawUnavailable(f"여비 규정을 조회하지 못했고 저장해 둔 규정도 없어요: {e}") from e
        reason = " ".join(f"{type(e).__name__}: {e}".split())[:500]  # MCP 오류 문구의 줄바꿈을 한 줄로
        log.warning("법령 조회 실패 — 저장해 둔 규정(MST %s)을 사용: %s", held[-1].mst, reason)
        _write_json(d / "status.json", status | {"online": False, "mst": held[-1].mst, "error": reason, "no_key": isinstance(e, LawKeyMissing)})
    else:
        prev = held[-1] if held else None
        save_snapshot(snap, d / "snapshots" / f"{snap.mst}.json")
        if prev is not None and prev.mst != snap.mst and snap.effective >= prev.effective:
            amendments = _read_json(d / "amendments.json", [])
            amendments.append({"from_mst": prev.mst, "to_mst": snap.mst, "effective": snap.effective.isoformat(), "detected_on": today.isoformat(),
                               "same_amounts": prev.rate_tables == snap.rate_tables and prev.params == snap.params})
            _write_json(d / "amendments.json", amendments)
            log.warning("법령 개정 감지: MST %s → %s (%s 시행)", prev.mst, snap.mst, snap.effective)
        _write_json(d / "status.json", status | {"online": True, "mst": snap.mst, "error": None})
    book = load_law_book(cache_dir, today, now=now, baseline_dir=baseline_dir)
    assert book is not None
    return book

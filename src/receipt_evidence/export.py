# src/receipt_evidence/export.py
from __future__ import annotations
import re
from datetime import date
from pathlib import Path
from PIL import Image
from .law import html_table_rows
from .mcp_client import ToolCaller
from .models import Receipt, ReceiptImage, TripConfig
from .report import DETAIL_HEADERS

ATTACHMENT_MAX_SIDE = 1600

def prepare_attachments(images: list[ReceiptImage], receipts: list[Receipt], dst: Path, max_side: int = ATTACHMENT_MAX_SIDE) -> dict[str, list[str]]:
    dst.mkdir(parents=True, exist_ok=True)
    pages: dict[str, list[ReceiptImage]] = {}
    for img in sorted(images, key=lambda i: (i.sha256, i.page)):
        pages.setdefault(img.sha256, []).append(img)
    sha_of = {i.image_id: i.sha256 for i in images}
    out: dict[str, list[str]] = {}
    for r in receipts:
        names = []
        for img in pages.get(sha_of.get(r.image_id, ""), []):
            name = f"{img.image_id}.jpg"
            with Image.open(img.png_path) as im:
                small = im.convert("RGB")
                small.thumbnail((max_side, max_side))
                small.save(dst / name, "JPEG", quality=85)
            names.append(name)
        out[r.receipt_id] = names
    return out

def parse_md_tables(md: str) -> list[list[list[str]]]:
    tables, cur = [], []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s[1:-1].split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            cur.append(cells)
        elif cur:
            tables.append(cur); cur = []
    if cur:
        tables.append(cur)
    for chunk in re.findall(r"<table.*?</table>", md, flags=re.S | re.I):
        rows = [r for r in html_table_rows(chunk) if r]
        if rows:
            tables.append(rows)
    return tables

def approved_total_from_md(md: str) -> int:
    for t in parse_md_tables(md):
        if t and t[0][:2] == DETAIL_HEADERS[:2]:
            col = t[0].index("인정액")
            row = next((r for r in t if r and r[0] == "합계"), None)
            if row:
                return int(re.sub(r"[^\d]", "", row[col]) or 0)
    raise ValueError("역파싱 결과에서 영수증별 상세 표의 합계 행을 찾지 못함")

def export_hwpx(caller: ToolCaller, markdown: str, out_path: Path, trip: TripConfig, image_dir: Path) -> Path:
    today = date.today()
    args = {"markdown": markdown, "output_path": str(out_path), "preset": "보고서", "image_dir": str(image_dir),
            "date": f"{today.year}. {today.month}. {today.day}.", "end_mark": False,
            "report_info": f"({today.year}. {today.month}. {today.day}., {trip.dept or trip.org or '소속 미기재'} {trip.traveler_name})"}
    if trip.org:
        args["org"] = trip.org
    if trip.approval:
        args["approval"] = trip.approval[:6]
    res = caller.call_many([("generate_document", args)])[0]
    if res.is_error or not out_path.exists():
        raise RuntimeError(f"generate_document 실패: {res.text}")
    return out_path

def verify_hwpx(caller: ToolCaller, hwpx_path: Path, expected_approved: int) -> dict:
    res = caller.call_many([("parse_document", {"file_path": str(hwpx_path)})])[0]
    if res.is_error:
        return {"ok": False, "parsed_total": None, "expected": expected_approved, "error": res.text}
    try:
        parsed = approved_total_from_md(res.text)
    except ValueError as e:
        return {"ok": False, "parsed_total": None, "expected": expected_approved, "error": str(e)}
    return {"ok": parsed == expected_approved, "parsed_total": parsed, "expected": expected_approved, "error": None}

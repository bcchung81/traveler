# src/receipt_evidence/versioning.py
from __future__ import annotations
import hashlib, json
from pathlib import Path
from .models import Decision, LawSnapshot, Receipt, TripConfig
from .report import REPORT_VERSION, fmt_won
from .rules import RULES_VERSION, totals

_RECEIPT_FIELDS = {"receipt_id", "sha256", "category", "merchant", "business_no", "amount", "paid_at", "service_date", "service_end_date",
                   "origin", "destination", "seat_class", "train_no", "approval_no", "region", "nights", "warnings"}

def fingerprint(receipts: list[Receipt], trip: TripConfig, law: LawSnapshot) -> str:
    payload = {
        "receipts": sorted((r.model_dump(mode="json", include=_RECEIPT_FIELDS) for r in receipts), key=lambda d: d["receipt_id"]),
        "trip": trip.model_dump(mode="json"),
        "law": [law.mst, law.effective.isoformat()],
        "rules": RULES_VERSION,
        "report": REPORT_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

def read_latest(trip_out: Path) -> dict | None:
    p = trip_out / "latest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

def plan_version(trip_out: Path, fp: str, *, force: bool = False) -> tuple[int, bool]:
    latest = read_latest(trip_out)
    if latest is None:
        return 1, True
    if latest["fingerprint"] == fp and not force:
        return int(latest["version"]), False
    return int(latest["version"]) + 1, True

def write_latest(trip_out: Path, version: int, fp: str) -> None:
    trip_out.mkdir(parents=True, exist_ok=True)
    (trip_out / "latest.json").write_text(json.dumps({"version": version, "fingerprint": fp}, ensure_ascii=False, indent=2), encoding="utf-8")

def load_decisions(path: Path) -> list[Decision]:
    if not path.exists():
        return []
    return [Decision.model_validate(d) for d in json.loads(path.read_text(encoding="utf-8"))]

def diff_markdown(prev: list[Decision], new: list[Decision], prev_version: int, new_version: int) -> str:
    key = lambda d: d.receipt_id or f"정액:{d.item}"
    p, n = {key(d): d for d in prev}, {key(d): d for d in new}
    added = [n[k] for k in n if k not in p]
    removed = [p[k] for k in p if k not in n]
    changed = [(p[k], n[k]) for k in n if k in p and (p[k].verdict, p[k].approved_amount, p[k].claimed_amount) != (n[k].verdict, n[k].approved_amount, n[k].claimed_amount)]
    tp, tn = totals(prev), totals(new)
    lines = [f"# 변경 내역 v{prev_version} → v{new_version}", "", "## 추가"]
    lines += [f"- {d.item}({key(d)}): 청구 {fmt_won(d.claimed_amount)}, 인정 {fmt_won(d.approved_amount)}, {d.verdict.value}" for d in added] or ["- 없음"]
    lines += ["", "## 삭제"]
    lines += [f"- {d.item}({key(d)}): 청구 {fmt_won(d.claimed_amount)}" for d in removed] or ["- 없음"]
    lines += ["", "## 판정 변경"]
    lines += [f"- {b.item}({key(b)}): {a.verdict.value} {fmt_won(a.approved_amount)} → {b.verdict.value} {fmt_won(b.approved_amount)}" for a, b in changed] or ["- 없음"]
    lines += ["", "## 합계", f"- 청구 {fmt_won(tp['claimed'])} → {fmt_won(tn['claimed'])}", f"- 인정 {fmt_won(tp['approved'])} → {fmt_won(tn['approved'])}",
              f"- 확인필요 {fmt_won(tp['review'])} → {fmt_won(tn['review'])}"]
    return "\n".join(lines) + "\n"

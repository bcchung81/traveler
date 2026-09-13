# src/receipt_evidence/validate.py
from __future__ import annotations
import re
from collections import Counter
from .models import Receipt

ERROR_CODES = frozenset({"MISSING_AMOUNT", "AMOUNT_NOT_IN_TRANSCRIPT", "BIZNO_CHECKSUM", "DUP_APPROVAL", "EXTRACT_FAILED", "MISSING_DATE"})
_W = [1, 3, 7, 1, 3, 7, 1, 3, 5]

def bizno_valid(s: str) -> bool:
    d = re.sub(r"\D", "", s or "")
    if len(d) != 10:
        return False
    n = [int(c) for c in d]
    total = sum(a * b for a, b in zip(n[:9], _W)) + (n[8] * 5) // 10
    return (10 - total % 10) % 10 == n[9]

def _confidence(warnings: list[str]) -> float:
    errors = sum(1 for w in warnings if w in ERROR_CODES)
    return round(max(0.0, 1.0 - 0.2 * errors), 2)

def validate_receipt(r: Receipt, transcript: str) -> Receipt:
    w = [x for x in r.warnings if x != "DUP_APPROVAL"]
    flat = re.sub(r"\s", "", transcript)
    if r.amount is None:
        w.append("MISSING_AMOUNT")
    elif f"{r.amount:,}" not in flat and str(r.amount) not in flat:
        w.append("AMOUNT_NOT_IN_TRANSCRIPT")
    if r.business_no is None:
        w.append("MISSING_BIZNO")
    elif not bizno_valid(r.business_no):
        w.append("BIZNO_CHECKSUM")
    if r.approval_no is None:
        w.append("MISSING_APPROVAL")
    elif re.sub(r"\D", "", r.approval_no) not in flat:
        w.append("APPROVAL_NOT_IN_TRANSCRIPT")
    if r.service_date is None and r.paid_at is None:
        w.append("MISSING_DATE")
    return r.model_copy(update={"warnings": w, "confidence": _confidence(w)})

def validate_all(receipts: list[Receipt], transcripts: dict[str, str]) -> list[Receipt]:
    out = [validate_receipt(r, transcripts.get(r.receipt_id, "")) for r in receipts]
    dup = {k for k, c in Counter(r.approval_no for r in out if r.approval_no).items() if c > 1}
    final = []
    for r in out:
        w = r.warnings + (["DUP_APPROVAL"] if r.approval_no in dup else [])
        final.append(r.model_copy(update={"warnings": w, "confidence": _confidence(w)}))
    return final

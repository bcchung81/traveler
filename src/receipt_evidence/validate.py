# src/receipt_evidence/validate.py
from __future__ import annotations
import re
from collections import Counter
from .models import Receipt

ERROR_CODES = frozenset({"MISSING_AMOUNT", "AMOUNT_NOT_IN_TRANSCRIPT", "BIZNO_CHECKSUM", "DUP_APPROVAL", "EXTRACT_FAILED", "MISSING_DATE"})
# 검사 묶음 → 그 묶음이 만드는 경고. 사용자가 고친 필드의 묶음만 다시 검사한다(다른 경고·해제 목록은 그대로 둔다).
GROUP_CODES: dict[str, frozenset[str]] = {
    "amount": frozenset({"MISSING_AMOUNT", "AMOUNT_NOT_IN_TRANSCRIPT"}),
    "business_no": frozenset({"MISSING_BIZNO", "BIZNO_CHECKSUM"}),
    "approval_no": frozenset({"MISSING_APPROVAL", "APPROVAL_NOT_IN_TRANSCRIPT"}),
    "date": frozenset({"MISSING_DATE"}),
}
FIELD_GROUP = {"amount": "amount", "business_no": "business_no", "approval_no": "approval_no", "service_date": "date", "paid_at": "date"}
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

def _amount_in(amount: int, transcript: str) -> bool:
    # 천 단위 쉼표를 걷어 낸 뒤 앞뒤가 숫자가 아닌 곳에서만 일치로 본다(48,200이 1,048,200 안에서 맞았다고 하지 않도록)
    flat = re.sub(r"(?<=\d),(?=\d{3})", "", re.sub(r"\s", "", transcript))
    return re.search(rf"(?<!\d){amount}(?!\d)", flat) is not None

def _approval_in(approval_no: str, transcript: str) -> bool:
    flat = re.sub(r"[\s\-]", "", transcript)
    key = re.sub(r"\D", "", approval_no) or re.sub(r"[\s\-]", "", approval_no)
    return bool(key) and key in flat

def _check(group: str, r: Receipt, transcript: str) -> list[str]:
    if group == "amount":
        if r.amount is None:
            return ["MISSING_AMOUNT"]
        return [] if _amount_in(r.amount, transcript) else ["AMOUNT_NOT_IN_TRANSCRIPT"]
    if group == "business_no":
        if r.business_no is None:
            return ["MISSING_BIZNO"]
        return [] if bizno_valid(r.business_no) else ["BIZNO_CHECKSUM"]
    if group == "approval_no":
        if r.approval_no is None:
            return ["MISSING_APPROVAL"]
        return [] if _approval_in(r.approval_no, transcript) else ["APPROVAL_NOT_IN_TRANSCRIPT"]
    return ["MISSING_DATE"] if r.service_date is None and r.paid_at is None else []

def validate_receipt(r: Receipt, transcript: str, only: set[str] | None = None) -> Receipt:
    """검사 묶음(only, 기본 전부)의 경고를 지우고 다시 계산한다. 여러 번 불러도 결과가 같다."""
    groups = [g for g in GROUP_CODES if only is None or g in only]
    drop = set().union(*(GROUP_CODES[g] for g in groups)) if groups else set()
    w = [x for x in r.warnings if x not in drop]
    for g in groups:
        w += [c for c in _check(g, r, transcript) if c not in w]
    return r.model_copy(update={"warnings": w, "confidence": _confidence(w)})

def mark_dup_approval(receipts: list[Receipt]) -> list[Receipt]:
    dup = {k for k, c in Counter(r.approval_no for r in receipts if r.approval_no).items() if c > 1}
    out = []
    for r in receipts:
        w = [x for x in r.warnings if x != "DUP_APPROVAL"] + (["DUP_APPROVAL"] if r.approval_no in dup else [])
        out.append(r if w == r.warnings else r.model_copy(update={"warnings": w, "confidence": _confidence(w)}))
    return out

def validate_all(receipts: list[Receipt], transcripts: dict[str, str]) -> list[Receipt]:
    return mark_dup_approval([validate_receipt(r, transcripts.get(r.receipt_id, "")) for r in receipts])

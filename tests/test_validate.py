# tests/test_validate.py
from datetime import date
from receipt_evidence.models import Receipt, Category
from receipt_evidence.validate import bizno_valid, validate_receipt, validate_all, ERROR_CODES

def _r(**kw):
    base = dict(receipt_id="a", image_id="a", category=Category.RAIL, amount=48200, business_no="314-82-10024", approval_no="55431218", service_date=date(2026, 7, 9))
    base.update(kw); return Receipt(**base)

def test_bizno_checksum():
    assert bizno_valid("314-82-10024") and not bizno_valid("314-82-10025") and not bizno_valid("12")

def test_clean_receipt_has_no_warnings():
    r = validate_receipt(_r(), "결제금액 48,200원 승인번호 55431218 사업자 314-82-10024")
    assert r.warnings == [] and r.confidence == 1.0

def test_amount_not_in_transcript():
    r = validate_receipt(_r(), "결제금액 48,000원 승인번호 55431218")
    assert "AMOUNT_NOT_IN_TRANSCRIPT" in r.warnings and r.confidence == 0.8

def test_missing_fields_and_bad_bizno():
    r = validate_receipt(_r(amount=None, business_no="111-11-11111", approval_no=None, service_date=None, paid_at=None), "")
    assert {"MISSING_AMOUNT", "BIZNO_CHECKSUM", "MISSING_APPROVAL", "MISSING_DATE"} <= set(r.warnings)
    assert r.confidence == 0.4  # error 3건(MISSING_AMOUNT, BIZNO_CHECKSUM, MISSING_DATE) × 0.2

def test_duplicate_approval_across_receipts():
    rs = validate_all([_r(receipt_id="a"), _r(receipt_id="b", image_id="b")], {"a": "48,200 55431218 314-82-10024", "b": "48,200 55431218 314-82-10024"})
    assert all("DUP_APPROVAL" in r.warnings for r in rs) and "DUP_APPROVAL" in ERROR_CODES

def test_revalidate_only_changed_groups_is_idempotent():
    r = validate_receipt(_r(), "결제금액 48,200원 승인번호 55431218 사업자 314-82-10024")
    again = validate_receipt(validate_receipt(r.model_copy(update={"amount": 50000}), "결제금액 48,200원 승인번호 55431218", only={"amount"}),
                             "결제금액 48,200원 승인번호 55431218", only={"amount"})
    assert again.warnings == ["AMOUNT_NOT_IN_TRANSCRIPT"]  # 사업자번호 그룹은 다시 보지 않아 원문에 없어도 경고 없음
    stale = _r(warnings=["MISSING_DATE", "EXTRACT_FAILED"])
    assert validate_receipt(stale, "", only={"date"}).warnings == ["EXTRACT_FAILED"]

def test_amount_and_approval_matching_is_strict():
    assert "AMOUNT_NOT_IN_TRANSCRIPT" in validate_receipt(_r(), "합계 1,048,200원 승인번호 55431218 314-82-10024").warnings
    assert "AMOUNT_NOT_IN_TRANSCRIPT" not in validate_receipt(_r(), "합계 48200원 승인번호 55431218 314-82-10024").warnings
    assert "APPROVAL_NOT_IN_TRANSCRIPT" in validate_receipt(_r(approval_no="ABC"), "48,200 314-82-10024").warnings
    assert "APPROVAL_NOT_IN_TRANSCRIPT" not in validate_receipt(_r(approval_no="ABC-12"), "48,200 승인 ABC-12 314-82-10024").warnings
    assert "APPROVAL_NOT_IN_TRANSCRIPT" not in validate_receipt(_r(), "48,200 승인 5543-1218 314-82-10024").warnings

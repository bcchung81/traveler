# tests/web/test_decision_feedback.py — '규정대로'인 채 입력하면 버려지던 문제, 저장 알림, 예전 서류 표시
from urllib.parse import unquote
import pytest, yaml
from helpers import new_trip

BASE = "/t/정백철/2026-07-09_서울"
JSON = {"Accept": "application/json"}
FILES = (("k1.png", (10, 20, 30)), ("stay.png", (70, 80, 90)))

def _trip_yaml(web):
    return yaml.safe_load((web.deps.service.trip_dir("정백철", "2026-07-09_서울") / "trip.yaml").read_text(encoding="utf-8"))

def test_rule_mode_with_new_reason_is_rejected_not_silently_dropped(web):
    new_trip(web)
    r = web.post(f"{BASE}/allowances/meal/decision", data={"mode": "규정대로", "days": "1", "reason": "둘째 날 식사 제공"}, headers=JSON)
    assert r.status_code == 400 and "판정을 골라" in r.json()["error"] and "allowance_decisions" not in _trip_yaml(web)
    stay = next(x.receipt_id for x in web.deps.service.receipts("정백철", "2026-07-09_서울") if x.amount == 48200)
    r = web.post(f"{BASE}/receipts/{stay}/decision", data={"verdict": "규정대로", "reason": "개인 일정"}, headers=JSON)
    assert r.status_code == 400 and "판정을 골라" in r.json()["error"]

def test_rule_mode_removes_existing_decision_even_with_its_reason(web):
    new_trip(web)
    web.post(f"{BASE}/allowances/meal/decision", data={"mode": "일수", "days": "1", "reason": "식사 제공"})
    assert "식비" in _trip_yaml(web)["allowance_decisions"]
    r = web.post(f"{BASE}/allowances/meal/decision", data={"mode": "규정대로", "days": "1", "reason": "식사 제공"})  # 화면이 기존 사유를 채워 보냄
    assert r.status_code == 303 and "allowance_decisions" not in _trip_yaml(web)

def test_saved_banner_after_decisions(web):
    new_trip(web, files=FILES)
    r = web.post(f"{BASE}/allowances/meal/decision", data={"mode": "일수", "days": "1", "reason": "식사 제공"})
    location = unquote(r.headers["location"])
    assert location == f"{BASE}/review?saved=a-meal#a-meal"
    page = web.get(location).text
    assert "식비를 25,000원(감액지급)으로 저장했어요" in page and 'class="banner banner--saved"' in page and 'is-saved' in page
    web.post(f"{BASE}/allowances/meal/decision", data={"mode": "규정대로"})
    assert "식비를 규정대로(지급 50,000원) 되돌렸어요" in web.get(f"{BASE}/review?saved=a-meal").text
    stay = next(x.receipt_id for x in web.deps.service.receipts("정백철", "2026-07-09_서울") if x.amount == 100000)
    web.post(f"{BASE}/receipts/{stay}/decision", data={"verdict": "불인정", "reason": "개인 숙박"})
    assert "숙박비를 0원(불인정)으로 저장했어요" in web.get(f"{BASE}/review?saved=d-{stay}").text
    assert "저장했어요" not in web.get(f"{BASE}/review?saved=a-nope").text

def test_review_form_markup_for_auto_select_and_mode_picker(web):
    new_trip(web)
    page = web.get(f"{BASE}/review").text
    assert 'id="dd-modes"' in page and "data-auto-mode" in page
    meal = page[page.index('id="a-meal"'):]
    assert 'name="days"' in meal and 'data-original="2"' in meal[:meal.index("판정 저장")]

def test_outdated_document_on_home_and_result(web):
    new_trip(web)
    web.post(f"{BASE}/finalize")
    home = web.get("/").text
    assert "서류 완성" in home and "148,200" in home and "서류 다시 만들기" not in home
    web.post(f"{BASE}/allowances/meal/decision", data={"mode": "일수", "days": "1", "reason": "식사 제공"})
    home = web.get("/").text
    assert "123,200" in home and "서류 다시 만들기" in home and "v1 서류 148,200" in home  # 현재 판정 금액 + 예전 서류 표시
    result = web.get(f"{BASE}/result").text
    assert "판정이 바뀌었어요" in result and "판정이 바뀌어 다시 만들어야 해요" in result and "123,200" in result and 'action="/t/' in result and "서류 다시 만들기" in result
    web.post(f"{BASE}/finalize")
    assert "판정이 바뀌었어요" not in web.get(f"{BASE}/result").text and "서류 다시 만들기" not in web.get("/").text

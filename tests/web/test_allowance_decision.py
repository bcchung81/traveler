# tests/web/test_allowance_decision.py — 일비·식비·근무지 내 출장 여비를 담당자가 일수·금액·사유로 고친다
from datetime import date
from urllib.parse import unquote
import pytest, yaml
from helpers import TRIP_CONFIRM, new_trip, upload_new
from receipt_evidence.workspace import NotFound

BASE = "/t/정백철/2026-07-09_서울"
JSON = {"Accept": "application/json"}

def _yaml(web):
    return yaml.safe_load((web.deps.service.trip_dir("정백철", "2026-07-09_서울") / "trip.yaml").read_text(encoding="utf-8"))

def test_review_shows_day_calc_and_allowance_editors(web):
    new_trip(web)
    page = web.get(f"{BASE}/review").text
    assert page.count("25,000×2일") >= 2 and 'id="a-daily"' in page and 'id="a-meal"' in page and 'data-unit="25000"' in page
    assert 'name="mode" value="일수"' in page and 'id="dd-days"' in page

def test_save_allowance_decision_validates(web):
    new_trip(web)
    s = web.deps.service
    with pytest.raises(ValueError, match="사유"):
        s.save_allowance_decision("정백철", "2026-07-09_서울", "meal", "일수", days="1", reason="")
    with pytest.raises(ValueError, match="일수"):
        s.save_allowance_decision("정백철", "2026-07-09_서울", "meal", "일수", days="1.5", reason="x")
    with pytest.raises(ValueError, match="금액"):
        s.save_allowance_decision("정백철", "2026-07-09_서울", "meal", "금액", amount="", reason="x")
    with pytest.raises(ValueError, match="금액으로만"):
        s.save_allowance_decision("정백철", "2026-07-09_서울", "incity", "일수", days="1", reason="x")
    with pytest.raises(NotFound):
        s.save_allowance_decision("정백철", "2026-07-09_서울", "nope", "금액", amount="1", reason="x")
    s.save_allowance_decision("정백철", "2026-07-09_서울", "meal", "일수", days="1", reason="둘째 날 식사 제공")
    s.save_allowance_decision("정백철", "2026-07-09_서울", "daily", "불인정", reason="공무 외 일정")
    assert _yaml(web)["allowance_decisions"] == {"식비": {"days": 1, "reason": "둘째 날 식사 제공"}, "일비": {"amount": 0, "reason": "공무 외 일정"}}
    s.save_trip_yaml("정백철", "2026-07-09_서울", {"purpose": "변경"})  # 다른 저장이 판정을 지우지 않는다
    assert "식비" in _yaml(web)["allowance_decisions"]
    s.save_allowance_decision("정백철", "2026-07-09_서울", "meal", "규정대로")
    s.save_allowance_decision("정백철", "2026-07-09_서울", "daily", "규정대로")
    y = _yaml(web)
    assert "allowance_decisions" not in y and y["end_date"] == date(2026, 7, 10)

def test_allowance_decision_via_review_json_and_totals(web):
    new_trip(web)
    r = web.post(f"{BASE}/allowances/meal/decision", data={"mode": "일수", "days": "1", "reason": ""}, headers=JSON)
    assert r.status_code == 400 and "사유" in r.json()["error"]
    r = web.post(f"{BASE}/allowances/meal/decision", data={"mode": "금액", "amount": "30,000", "reason": "중식 1회 제공"}, headers=JSON)
    assert r.status_code == 200 and unquote(r.json()["redirect"]) == f"{BASE}/review?saved=a-meal#a-meal"
    page = web.get(f"{BASE}/review").text
    assert "128,200" in page and "chip--manual" in page and "30,000원 지정" in page  # 48,200 + 일비 50,000 + 식비 30,000
    r = web.post(f"{BASE}/allowances/meal/decision", data={"mode": "규정대로"})
    assert r.status_code == 303 and "148,200" in web.get(f"{BASE}/review").text

def test_in_city_allowance_editor_has_no_days(web):
    upload_new(web)
    r = web.post(f"{BASE}/trip", data=TRIP_CONFIRM | {"destination_region": "나주", "workplace_region": "나주"})
    base = unquote(r.headers["location"]).rsplit("/", 1)[0]
    web.post(f"{base}/resolve", data={"within_workplace": "on", "duration_hours": "5"})
    page = web.get(f"{base}/review").text
    assert 'id="a-incity"' in page and 'value="금액"' in page and 'name="mode" value="일수"' not in page

def test_unconfirmed_trip_banner_and_reliable_allowances(web):
    upload_new(web, files=(("k1.png", (10, 20, 30)), ("k2.png", (40, 50, 60))))
    page = web.get(f"{BASE}/review").text
    assert "자동 제안값" in page and "196,400" in page and "자동 제안 기간" in page  # 왕복 표가 있어 확정 전에도 일비·식비 지급
    upload_new(web, files=(("k9.png", (10, 20, 30)),), traveler="홍길동")
    page = web.get("/t/홍길동/2026-07-09_서울/review").text
    assert "근거 부족" in page and "48,200" in page  # 가는 편만 있으면 확인필요

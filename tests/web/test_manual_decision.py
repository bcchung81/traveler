# tests/web/test_manual_decision.py — 판정 화면에서 담당자가 인정·감액·불인정을 정한다
from urllib.parse import quote, unquote
import pytest, yaml
from helpers import new_trip, spec
from receipt_evidence.workspace import NotFound

BASE = "/t/정백철/2026-07-09_서울"
FILES = (("k1.png", (10, 20, 30)), ("stay.png", (70, 80, 90)))

def _rid(web, amount):
    return next(r.receipt_id for r in web.deps.service.receipts("정백철", "2026-07-09_서울") if r.amount == amount)

def test_save_decision_validates_and_keeps_other_overrides(web):
    new_trip(web, files=FILES)
    s, stay = web.deps.service, _rid(web, 100000)
    s.save_override("정백철", "2026-07-09_서울", stay, {"region": "서울"}, clear_warnings=["MISSING_BIZNO"])
    with pytest.raises(ValueError, match="사유"):
        s.save_decision("정백철", "2026-07-09_서울", stay, "지급", reason=" ")
    with pytest.raises(ValueError, match="중 하나"):
        s.save_decision("정백철", "2026-07-09_서울", stay, "마음대로", reason="x")
    with pytest.raises(ValueError, match="청구액 100,000원"):
        s.save_decision("정백철", "2026-07-09_서울", stay, "감액지급", amount="120,000", reason="x")
    with pytest.raises(NotFound):
        s.save_decision("정백철", "2026-07-09_서울", "nope-p1", "지급", reason="x")
    s.save_decision("정백철", "2026-07-09_서울", stay, "감액지급", amount="80,000", reason="1박분만 인정")
    s.save_override("정백철", "2026-07-09_서울", stay, {"merchant": "예시호텔"}, clear_warnings=None)  # 읽은 값 수정이 판정을 지우지 않는다
    entry = yaml.safe_load((s.trip_dir("정백철", "2026-07-09_서울") / "overrides.yaml").read_text(encoding="utf-8"))[stay]
    assert entry["decision"] == {"verdict": "감액지급", "reason": "1박분만 인정", "approved_amount": 80000}
    assert entry["region"] == "서울" and entry["merchant"] == "예시호텔" and entry["clear_warnings"] == ["MISSING_BIZNO"]
    s.save_decision("정백철", "2026-07-09_서울", stay, "규정대로")
    entry = yaml.safe_load((s.trip_dir("정백철", "2026-07-09_서울") / "overrides.yaml").read_text(encoding="utf-8"))[stay]
    assert "decision" not in entry and entry["region"] == "서울"

def test_review_page_manual_decision_flow(web):
    new_trip(web, files=FILES)
    stay = _rid(web, 100000)
    page = web.get(f"{BASE}/review").text
    assert page.count("판정 바꾸기") == 2 and "148,200" in page  # 영수증 행에만(일비·식비 제외)
    assert f"?edit={quote(stay, safe='')}#d-{stay}" in page and "담당자 판정으로 정하기" in page  # 확인필요에서 바로 가기
    assert f'id="d-{stay}" open' in web.get(f"{BASE}/review", params={"edit": stay}).text
    r = web.post(f"{BASE}/receipts/{stay}/decision", data={"verdict": "지급", "reason": "체크인 7/9 확인"})
    assert r.status_code == 303 and unquote(r.headers["location"]) == f"{BASE}/review#d-{stay}"
    page = web.get(f"{BASE}/review").text
    assert "248,200" in page and "잠깐, 확인!" not in page and "chip--manual" in page and "체크인 7/9 확인" in page
    assert "규정상 확인필요 0원" in page
    assert web.post(f"{BASE}/receipts/{stay}/decision", data={"verdict": "지급", "reason": ""}).status_code == 400

def test_over_rule_badge_over_cap_reason_and_document(web):
    web.vlm_fake.specs[(1, 2, 3)] = spec("숙박", 150000, "7700001", merchant="예시호텔", service_date="2026-07-09", region="서울", nights=1)
    new_trip(web, files=(("k1.png", (10, 20, 30)), ("hotel.png", (1, 2, 3))))
    hotel = _rid(web, 150000)
    page = web.get(f"{BASE}/review").text
    assert "상한 초과 사유" in page  # 규정상 감액지급(상한 100,000) — 추가지급 사유를 넣을 곳
    web.post(f"{BASE}/resolve", data={"over_cap_reason": "행사장 인근 만실"})
    assert "130,000" in web.get(f"{BASE}/review").text  # 상한의 10분의 3까지 규정대로 추가지급
    web.post(f"{BASE}/receipts/{hotel}/decision", data={"verdict": "지급", "reason": "기관장 승인"})
    page = web.get(f"{BASE}/review").text
    assert "규정 초과" in page and "150,000" in page
    web.post(f"{BASE}/finalize")
    result = web.deps.service.latest_result("정백철", "2026-07-09_서울")
    md = open(result.report_md_path, encoding="utf-8").read()
    assert "## 담당자 판정 내역" in md and "[규정 한도 초과 인정]" in md and "사유: 기관장 승인" in md

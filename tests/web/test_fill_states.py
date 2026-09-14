# tests/web/test_fill_states.py — 읽은 값 화면: AI가 읽은 값·추정·고친 값·빈칸을 입력칸 배경으로 구분
from urllib.parse import unquote
from helpers import TRIP_CONFIRM, upload_new

BASE = "/t/정백철/2026-07-09_서울"

def _field(page, input_id):
    """입력칸 태그 한 개(여는 태그)를 돌려준다."""
    start = page.index(f'id="{input_id}"')
    return page[page.rindex("<", 0, start):page.index(">", start) + 1]

def test_receipt_fields_show_ai_empty_and_user_states(web):
    upload_new(web, files=(("k1.png", (10, 20, 30)), ("stay.png", (70, 80, 90))))
    s = web.deps.service
    rail = next(r for r in s.receipts("정백철", "2026-07-09_서울") if r.amount == 48200)
    page = web.get(f"{BASE}/extract", params={"rid": rail.receipt_id}).text
    assert 'class="fill-legend"' in page and "AI가 읽은 값" in page and "AI 추정" in page and "고친 값" in page and "못 읽은 칸" in page
    for name in ("merchant", "amount", "approval_no", "business_no", "paid_at", "service_date", "origin", "destination", "train_no", "seat_class"):
        assert 'data-fill="ai"' in _field(page, name), name
    assert 'data-fill="ai"' in _field(page, "category") and 'data-original="한국철도공사"' in _field(page, "merchant")
    stay = next(r for r in s.receipts("정백철", "2026-07-09_서울") if r.amount == 100000)
    web.post(f"{BASE}/receipts/{stay.receipt_id}", data={"region": "서울", "service_date": "2026-08-21"})
    page = web.get(f"{BASE}/extract", params={"rid": stay.receipt_id}).text
    assert 'data-fill="empty"' in _field(page, "business_no") and 'data-fill="empty"' in _field(page, "nights")  # AI가 못 읽음
    assert 'data-fill="user"' in _field(page, "region") and 'data-fill="ai"' in _field(page, "merchant")
    assert 'data-fill="ai"' in _field(page, "service_date")  # AI 값과 같은 값으로 저장하면 고친 값이 아니다

def test_keyword_inferred_category_is_marked_as_guess(web):
    web.vlm_fake.specs[(5, 5, 5)] = {**web.vlm_fake.specs[(70, 80, 90)], "category": "기타", "merchant": "(주)여기어때컴퍼니"}
    upload_new(web, files=(("mail.png", (5, 5, 5)),))
    location = next(iter(web.deps.service.list_trips())).trip_id
    page = web.get(f"/t/정백철/{location}/extract").text
    tag = _field(page, "category")
    assert 'data-fill="guess"' in tag and "여기어때" in tag  # VLM이 '기타'라 답해 키워드로 숙박으로 정한 값

def test_trip_card_autofill_states(web):
    upload_new(web, files=(("k1.png", (10, 20, 30)), ("k2.png", (40, 50, 60))))
    page = web.get(f"{BASE}/extract").text
    assert 'data-fill="ai"' in _field(page, "t_start") and 'data-fill="ai"' in _field(page, "t_end") and 'data-fill="ai"' in _field(page, "t_dest")
    assert 'data-fill="guess"' in _field(page, "t_work")  # 첫 출발역으로 추정한 근무지
    web.post(f"{BASE}/trip", data=TRIP_CONFIRM)
    page = web.get(f"{BASE}/extract").text
    assert 'data-fill="user"' in _field(page, "t_start") and 'data-fill="user"' in _field(page, "t_work")  # 확정한 값

def test_edit_script_and_styles_present(web):
    upload_new(web)
    page = web.get(f"{BASE}/extract").text
    assert "data-fill-edit" in page and "fill--edited" in page
    css = web.get("/static/app.css").text
    for cls in (".fill--ai", ".fill--guess", ".fill--user", ".fill--empty", ".fill--edited", ".fill-legend"):
        assert cls in css, cls

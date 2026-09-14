# tests/web/test_trash_and_cards.py — 출장 삭제(휴지통)와 홈 카드
from urllib.parse import quote, unquote
from helpers import new_trip
from receipt_evidence.web.jobs import Job

BASE = "/t/정백철/2026-07-09_서울"

def test_home_card_is_compact_with_delete_button(web):
    new_trip(web)
    home = unquote(web.get("/").text)
    assert 'class="panel trip-card"' in home and "trip-card__link" in home and 'data-confirm-action="/t/정백철/2026-07-09_서울/trash"' in home
    assert f'href="{BASE}/delete"' in home and 'id="confirm-dialog"' in home and "sticker--right" not in home and "출장 정보 미확정" not in home
    css = web.get("/static/app.css").text
    assert ".trip-card { " in css and "min-height: 154px" in css and ".trip-form-grid" in css
    assert "trip-form-grid" in web.get(f"{BASE}/extract").text and "trip-grid" not in web.get(f"{BASE}/extract").text.replace("trip-form-grid", "")

def test_delete_confirm_trash_undo_restore_and_purge(web):
    new_trip(web)
    s = web.deps.service
    confirm = web.get(f"{BASE}/delete")
    assert confirm.status_code == 200 and "휴지통으로 옮기기" in confirm.text and "영수증 1건" in confirm.text  # 스크립트 없는 확인 화면
    r = web.post(f"{BASE}/trash")
    location = unquote(r.headers["location"])
    assert r.status_code == 303 and location.startswith("/?trashed=")
    home = web.get(location).text
    assert "휴지통으로 옮겼어요" in home and "되돌리기" in home and "2026-07-09_서울" in home and "아직 출장이 없어요" in home
    assert "휴지통 1" in home and not s.trip_dir("정백철", "2026-07-09_서울").exists()
    trash = web.get("/trash").text
    [entry] = s.trash_entries()
    assert "2026-07-09_서울" in trash and f"/trash/{quote(entry['id'], safe='')}/restore" in unquote(trash) or f"/trash/{entry['id']}/restore" in unquote(trash)
    r = web.post(f"/trash/{quote(entry['id'], safe='')}/restore")
    assert r.status_code == 303 and s.trip_dir("정백철", "2026-07-09_서울").exists() and s.trash_entries() == []
    assert "148,200" in web.get(f"{BASE}/review").text  # 복구하면 판정·확인값 그대로
    web.post(f"{BASE}/trash")
    [entry] = s.trash_entries()
    assert web.post(f"/trash/{quote(entry['id'], safe='')}/purge").status_code == 303 and s.trash_entries() == []
    assert web.post("/trash/20990101-000000_x_y/purge").status_code == 404

def test_upload_page_has_delete_and_running_job_blocks_trash(web):
    new_trip(web)
    assert 'data-confirm-action="/t/정백철/2026-07-09_서울/trash"' in unquote(web.get(f"{BASE}/upload").text)
    web.deps.jobs._jobs["정백철/2026-07-09_서울"] = Job(key="정백철/2026-07-09_서울", kind="extract", state="running")
    r = web.post(f"{BASE}/trash")
    assert r.status_code == 400 and "작업" in r.text and web.deps.service.trip_dir("정백철", "2026-07-09_서울").exists()

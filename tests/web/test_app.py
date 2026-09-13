# tests/web/test_app.py
from helpers import FakeProc

def test_home_empty_and_vlm_badge(web):
    r = web.get("/")
    assert r.status_code == 200 and "새 정산" in r.text and "아직 출장이 없어요" in r.text and 'id="vlm-status"' in r.text
    assert '/static/htmx.min.js' in r.text and "준비됨" in web.get("/vlm").text
    assert web.get("/static/app.css").status_code == 200

def test_vlm_badge_states_and_no_start_button(web):
    web.deps.vlm.health = lambda: False
    r = web.get("/vlm")
    assert "꺼짐" in r.text and "필요할 때 자동으로 켜요" in r.text and "/vlm/start" not in r.text
    web.deps.vlm._proc = FakeProc()
    r = web.get("/vlm")
    assert "켜는 중" in r.text and 'hx-trigger="every 3s"' in r.text

def test_security_host_origin_and_paths(web):
    assert web.get("/", headers={"host": "evil.example"}).status_code == 400
    assert web.post("/new", data={}, headers={"origin": "http://evil.example"}).status_code == 403
    assert web.post("/new", data={}, headers={"origin": "http://testserver"}).status_code != 403  # 같은 출처는 통과

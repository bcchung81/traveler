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

import json
import pytest
from datetime import date

def test_bad_input_shows_html_error_page(web):
    r = web.post("/new", data={"traveler_new": ".숨김"}, files=[("files", ("k.png", b"x", "image/png"))])
    assert r.status_code == 400 and "text/html" in r.headers["content-type"] and "입력" in r.text and 'href="/"' in r.text

def test_code_bugs_are_not_hidden_as_404(web, monkeypatch):
    monkeypatch.setattr(web.deps.service, "list_trips", lambda: {}["boom"])
    with pytest.raises(KeyError):
        web.get("/")

def test_no_cdn_fonts_and_local_fonts_served(web):
    r = web.get("/")
    assert "fonts.googleapis.com" not in r.text
    css = web.get("/static/app.css").text
    assert "@font-face" in css
    for name in ("GothicA1-Regular.woff2", "DoHyeon-Regular.woff2", "Anton-Regular.woff2"):
        assert web.get(f"/static/fonts/{name}").status_code == 200, name

def test_home_shows_law_amendment_banner(web):
    law_dir = web.deps.service.out_dir / ".cache" / "law"; law_dir.mkdir(parents=True)
    (law_dir / "amendments.json").write_text(json.dumps([{"from_mst": "287535", "to_mst": "299999", "effective": "2026-09-01",
                                                          "detected_on": date.today().isoformat(), "same_amounts": False}]), encoding="utf-8")
    assert "여비 규정이 개정됐어요" in web.get("/").text

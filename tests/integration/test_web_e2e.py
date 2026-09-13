import shutil, time, unicodedata
from pathlib import Path
from urllib.parse import unquote
import pytest
from fastapi.testclient import TestClient
from receipt_evidence.vlm import LlamaServerClient
from receipt_evidence.web.app import WebSettings, create_app, default_deps

ROOT = Path(__file__).resolve().parents[2]
SRC = {unicodedata.normalize("NFC", p.name): p for p in (ROOT / "data").rglob("*") if p.is_file()}
MODEL = Path.home() / ".cache/huggingface/hub/models--Qwen--Qwen3-VL-8B-Instruct-GGUF/snapshots/f982a07559d4a2f6c8744d840bf6fccab30eea96/Qwen3VL-8B-Instruct-Q4_K_M.gguf"
BASE = "/t/정백철/2026-07-09_서울"
FILES = ("Screenshot_20260713_083024.jpg", "Screenshot_20260713_083037.jpg", "숙박 영수증.pdf")

def _vlm_up() -> bool:
    return LlamaServerClient(timeout=2.0).healthy()

def _wait_job(client, timeout: float = 900):
    """읽기가 끝나면 임시 폴더가 2026-07-09_서울로 바뀌므로, 최종 주소에서 작업 끝을 기다린다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get(f"{BASE}/job").headers.get("HX-Refresh") == "true":
            return
        time.sleep(2)
    raise AssertionError("작업이 제한 시간 안에 끝나지 않음")

@pytest.mark.integration
def test_web_e2e_real_receipts_on_demand_vlm(tmp_path):
    if not MODEL.exists() or shutil.which("llama-server") is None or not all(n in SRC for n in FILES):
        pytest.skip("모델·llama-server·실제 영수증이 없음")
    if _vlm_up():
        pytest.skip("llama-server가 이미 떠 있음 — 필요할 때만 켜고 끄는지 검증하려면 끄고 실행")
    settings = WebSettings(data_dir=tmp_path / "data", out_dir=tmp_path / "out", allowed_hosts=["testserver"])
    deps = default_deps(settings)
    with TestClient(create_app(settings, deps), follow_redirects=False) as client:
        files = [("files", (name, SRC[name].read_bytes(), "application/octet-stream")) for name in FILES]
        r = client.post("/new", data={"traveler_new": "정백철"}, files=files)  # 첫 화면: 출장자와 영수증만
        assert r.status_code == 303 and "/_새정산-" in unquote(r.headers["location"])
        _wait_job(client)
        assert not _vlm_up()  # 다 읽고 나면 웹앱이 켠 llama-server를 끈다
        page = client.get(f"{BASE}/extract").text  # 실제 영수증으로 기간·출장지를 채워 폴더 이름이 바뀌었다
        assert "AI가 읽은 값" in page and 'value="2026-07-09"' in page and 'value="2026-07-10"' in page and 'value="서울"' in page
        assert 'value="나주"' in page and deps.service.load_profile("정백철").grade == "제2호"
        receipts = deps.service.receipts("정백철", "2026-07-09_서울")
        assert len(receipts) == 3
        client.post(f"{BASE}/trip", data={"start_date": "2026-07-09", "end_date": "2026-07-10", "destination_region": "서울",
                                          "workplace_region": "나주", "grade": "제2호", "route_stations": "나주, 용산"})
        client.post(f"{BASE}/docinfo", data={"purpose": "회의", "approval": "담당, 팀장, 부장"})

        review = client.get(f"{BASE}/review").text
        assert "196,400" in review and "잠깐, 확인!" in review
        stay = next(r for r in receipts if r.category.value == "숙박")
        client.post(f"{BASE}/receipts/{stay.receipt_id}", data={"service_date": "2026-07-09", "region": "서울", "next": "review"})
        assert "296,400" in client.get(f"{BASE}/review").text

        assert client.post(f"{BASE}/finalize").status_code == 303
        _wait_job(client)
        result = unquote(client.get(f"{BASE}/result").text)
        assert "서류가 완성됐어요" in result and "합계를 다시 읽어 맞춰 봤어요" in result and "296,400" in result
        assert client.get(f"{BASE}/v/1/preview").status_code == 200
        download = client.get(f"{BASE}/v/1/evidence.hwpx")
        assert download.status_code == 200 and len(download.content) > 100_000
        assert "서류 완성" in client.get("/").text
    assert not _vlm_up()

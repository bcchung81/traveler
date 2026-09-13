# tests/web/conftest.py
from contextlib import contextmanager
from datetime import date
import pytest
from fastapi.testclient import TestClient
from helpers import ColorVlm, doc_fake, law_from, rail, spec
from receipt_evidence.pipeline import Clients
from receipt_evidence.web.app import WebDeps, WebSettings, create_app
from receipt_evidence.web.jobs import JobManager
from receipt_evidence.web.service import TripService
from receipt_evidence.web.vlm_process import VlmManager

SPECS = {(10, 20, 30): rail(1, date(2026, 7, 9), "나주", "용산"), (40, 50, 60): rail(2, date(2026, 7, 10), "용산", "나주"),
         (70, 80, 90): spec("숙박", 100000, "68325420", merchant="(주)예시숙박", service_date="2026-08-21")}

@pytest.fixture
def web(tmp_path, law_fixture_text):
    settings = WebSettings(data_dir=tmp_path / "data", out_dir=tmp_path / "out", allowed_hosts=["testserver"])
    vlm = ColorVlm(SPECS)
    @contextmanager
    def clients():
        yield Clients(vlm=vlm, law=law_from(law_fixture_text), doc=doc_fake(render=True))
    deps = WebDeps(service=TripService(settings.data_dir, settings.out_dir), jobs=JobManager(inline=True),
                   vlm=VlmManager(health=lambda: True, start_cmd=["true"], cwd=tmp_path, popen=lambda *a, **k: None), clients=clients)
    with TestClient(create_app(settings, deps), follow_redirects=False) as client:
        client.deps, client.vlm_fake = deps, vlm
        yield client

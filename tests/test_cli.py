# tests/test_cli.py
from receipt_evidence import cli
from receipt_evidence.mcp_client import FakeToolCaller
from receipt_evidence.models import BatchResult, PipelineResult, TripConfig

def _res(**kw):
    base = dict(run_id="b", traveler="정백철", trip_id="2026-07-09_서울", trip=TripConfig(traveler_name="정백철"), law_mst="287535",
                law_effective="2026-07-01", receipts=[], decisions=[], totals={"claimed": 196400, "approved": 196400, "review": 100000},
                review_items=["[stay] 숙박비: 지역 미확인"], report_md_path="r.md", hwpx_path="out/정백철/2026-07-09_서울/v1/evidence.hwpx",
                version=1, verify_ok=True)
    return PipelineResult(**(base | kw))

def _patch(monkeypatch, results, captured=None):
    def fake_batch(data_dir, out_dir, clients, *, travelers=None, trips=None, opts=None, run_id=None, **kwargs):
        if captured is not None:
            captured.update(travelers=travelers, trips=trips, workers=opts.workers, new_version=opts.new_version, refresh_law=opts.refresh_law)
        return BatchResult(run_id="b", results=results, warnings=["loose.jpg: data/<출장자>/<출장>/ 폴더에 넣어야 처리돼요"],
                           summary_md_path="out/summary-b.md", summary_json_path="out/summary-b.json")
    monkeypatch.setattr(cli, "run_batch", fake_batch)
    monkeypatch.setattr(cli, "law_caller", lambda: FakeToolCaller({}))
    monkeypatch.setattr(cli, "kordoc_caller", lambda: FakeToolCaller({}))

def test_parser_defaults():
    a = cli.build_parser().parse_args(["run"])
    assert (a.data, a.out, a.workers, a.traveler, a.trip, a.new_version, a.refresh_law, a.vlm_url) == (
        "data", "out", 1, None, None, False, False, "http://127.0.0.1:8088")

def test_run_prints_each_trip_and_passes_filters(monkeypatch, capsys):
    captured = {}
    _patch(monkeypatch, [_res()], captured)
    code = cli.main(["run", "--traveler", "정백철", "--trip", "2026-07-09_서울", "--workers", "2", "--new-version", "--refresh-law"])
    out = capsys.readouterr().out
    assert code == 0 and "정백철/2026-07-09_서울 v1 새 버전" in out and "확인필요 1건" in out and "loose.jpg" in out and "summary-b.md" in out
    assert captured == {"travelers": ["정백철"], "trips": ["2026-07-09_서울"], "workers": 2, "new_version": True, "refresh_law": True}

def test_exit_codes(monkeypatch):
    _patch(monkeypatch, [_res(verify_ok=False)])
    assert cli.main(["run"]) == 3
    _patch(monkeypatch, [_res(), _res(trip_id="2026-08-03_부산", error="RuntimeError: kordoc 실패", verify_ok=None, hwpx_path=None)])
    assert cli.main(["run"]) == 2

def test_check_vlm_unhealthy(monkeypatch):
    class Dead:
        def __init__(self, *a, **k): ...
        def healthy(self): return False
    monkeypatch.setattr(cli, "LlamaServerClient", Dead)
    assert cli.main(["check-vlm"]) == 2

def test_run_uses_on_demand_vlm_and_stops(monkeypatch):
    events = []
    class FakeVlm:
        def ensure_ready(self, timeout=180.0, poll=1.0):
            events.append("ensure")
        def stop(self):
            events.append("stop")
    _patch(monkeypatch, [_res()])
    monkeypatch.setattr(cli, "vlm_manager", lambda url: FakeVlm())
    inner = cli.run_batch
    def batch(*a, **k):
        k["on_vlm_needed"]()  # 새 영수증이 있어 VLM이 필요한 상황
        return inner(*a, **k)
    monkeypatch.setattr(cli, "run_batch", batch)
    assert cli.main(["run"]) == 0 and events == ["ensure", "stop"]

def test_web_command_refuses_non_loopback(monkeypatch):
    called = {}
    monkeypatch.setattr(cli, "_serve_web", lambda settings: called.setdefault("s", settings))
    assert cli.main(["web", "--host", "0.0.0.0"]) == 2 and "s" not in called
    assert cli.main(["web", "--port", "9000"]) == 0 and called["s"].port == 9000 and called["s"].host == "127.0.0.1"

def test_run_prints_law_notices(monkeypatch, capsys):
    _patch(monkeypatch, [_res()])
    inner = cli.run_batch
    monkeypatch.setattr(cli, "run_batch", lambda *a, **k: inner(*a, **k).model_copy(update={"notices": ["인터넷에 연결되지 않아 저장해 둔 규정을 적용함"]}))
    assert cli.main(["run"]) == 0 and "※ 인터넷에 연결되지 않아" in capsys.readouterr().out

def test_prepare_fetches_law_and_starts_mcp_packages(monkeypatch, capsys, tmp_path, law_fixture_text):
    from helpers import law_from
    started = []
    class Doc(FakeToolCaller):
        def ensure_started(self):
            started.append("kordoc")
    monkeypatch.setattr(cli, "law_caller", lambda: law_from(law_fixture_text))
    monkeypatch.setattr(cli, "kordoc_caller", lambda: Doc({}))
    monkeypatch.setattr(cli, "vlm_ready", lambda: (True, "모델 파일 있음"))
    code = cli.main(["prepare", "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert code == 0 and "최신본" in out and "287535" in out and started == ["kordoc"] and "오프라인" in out
    class Down(FakeToolCaller):
        def call_many(self, calls):
            raise RuntimeError("ENOTFOUND")
    monkeypatch.setattr(cli, "law_caller", lambda: Down({}))
    assert cli.main(["prepare", "--out", str(tmp_path / "out")]) == 2 and "저장해 둔 규정" in capsys.readouterr().out

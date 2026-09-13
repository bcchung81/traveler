# tests/web/test_jobs.py
import threading, time
import pytest
from helpers import FakeProc
from receipt_evidence.web.jobs import JobManager
from receipt_evidence.web.vlm_process import VlmManager

def _boom():
    raise RuntimeError("llama-server가 응답하지 않음")

def test_inline_jobs_record_result_and_error():
    jm = JobManager(inline=True)
    j = jm.submit("a", "extract", lambda: 3)
    assert (j.state, j.result) == ("done", 3) and j.finished_at is not None
    e = jm.submit("b", "extract", _boom)
    assert e.state == "error" and "llama-server" in e.message and jm.get("b") is e

def test_threaded_jobs_dedupe_running_key():
    gate = threading.Event()
    jm = JobManager()
    try:
        j1 = jm.submit("t", "extract", lambda: gate.wait(5) and "ok")
        assert jm.submit("t", "extract", lambda: "second") is j1 and j1.state in ("queued", "running")
        gate.set()
        for _ in range(200):
            if j1.state == "done":
                break
            time.sleep(0.01)
        assert j1.state == "done" and j1.result == "ok"
        assert jm.submit("t", "finalize", lambda: "new") is not j1
    finally:
        jm.shutdown()

def test_vlm_manager_ensure_ready_starts_waits_and_stops(tmp_path):
    polls, started = {"n": 0}, []
    def health():
        polls["n"] += 1; return polls["n"] > 3        # 세 번째 확인까지는 아직 로딩 중
    def popen(cmd, **kw):
        started.append((cmd, kw["cwd"])); return FakeProc()
    m = VlmManager(health=health, start_cmd=["bash", "scripts/start_vlm.sh"], cwd=tmp_path, popen=popen, sleep=lambda s: None)
    m.ensure_ready(timeout=10)
    assert len(started) == 1 and m.status() == "ready"
    proc = m._proc; m.stop()
    assert proc.terminated and m._proc is None

def test_vlm_manager_leaves_external_server_alone(tmp_path):
    started = []
    m = VlmManager(health=lambda: True, start_cmd=["x"], cwd=tmp_path, popen=lambda *a, **k: started.append(1))
    m.ensure_ready()
    assert started == []
    m.stop()  # 남이 켠 서버는 끄지 않는다

def test_vlm_manager_timeout_and_crash(tmp_path):
    t = {"now": 0.0}
    def sleep(s):
        t["now"] += s
    m = VlmManager(health=lambda: False, start_cmd=["x"], cwd=tmp_path, popen=lambda *a, **k: FakeProc(), sleep=sleep, clock=lambda: t["now"])
    with pytest.raises(RuntimeError, match="준비되지"):
        m.ensure_ready(timeout=5, poll=1)
    assert m._proc is None  # 시간 초과 시 띄운 프로세스를 정리
    dead = FakeProc(); dead.alive = False
    m2 = VlmManager(health=lambda: False, start_cmd=["x"], cwd=tmp_path, popen=lambda *a, **k: dead, sleep=lambda s: None)
    with pytest.raises(RuntimeError, match="종료"):
        m2.ensure_ready(timeout=5)

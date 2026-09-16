# tests/test_vlm_server.py — 맥·윈도우 공통 로컬 AI 실행(llama-server 명령·실행 파일 찾기·로그·필요할 때만 켜기)
import os, re, subprocess
from pathlib import Path
import pytest
from helpers import FakeProc
from receipt_evidence import vlm_models, vlm_server
from receipt_evidence.web.vlm_process import default_vlm_manager

ROOT = Path(__file__).resolve().parents[1]

def _models(hub: Path, key: str = "4b") -> tuple[Path, Path]:
    v = vlm_models.VARIANTS[key]
    snap = hub / f"models--{v.repo.replace('/', '--')}" / "snapshots" / "s1"
    snap.mkdir(parents=True)
    (snap / v.model_file).write_bytes(b"x"); (snap / v.mmproj_file).write_bytes(b"x")
    return snap / v.model_file, snap / v.mmproj_file

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in ("LLAMA_SERVER", "VLM_MODEL", "VLM_MMPROJ", "VLM_VARIANT", "VLM_PARALLEL", "VLM_CTX", "VLM_LOG"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.setattr(vlm_server, "LOCAL_LLAMA_DIR", tmp_path / "tools" / "llama.cpp")
    exe = tmp_path / "bin" / vlm_server.EXE
    exe.parent.mkdir(); exe.write_bytes(b"")
    return tmp_path, exe

def test_command_uses_same_options_as_start_script(env, monkeypatch):
    tmp, exe = env
    model, mmproj = _models(tmp / "hub")
    monkeypatch.setenv("LLAMA_SERVER", str(exe))
    cmd = vlm_server.command(8099)
    assert cmd[:5] == [str(exe), "-m", str(model), "--mmproj", str(mmproj)]
    script = (ROOT / "scripts" / "start_vlm.sh").read_text(encoding="utf-8")
    flags = re.search(r'--host 127\.0\.0\.1 --port "\$PORT" \\\n\s+-c "\$CTX" (.+?) >', script).group(1)
    assert " ".join(cmd[5:]) == f"--host 127.0.0.1 --port 8099 -c 12288 {flags.replace(chr(34) + '$PARALLEL' + chr(34), '1')}"
    monkeypatch.setenv("VLM_PARALLEL", "4")
    assert vlm_server.command()[vlm_server.command().index("-c") + 1] == str(12288 * 4)

def test_executable_lookup_order(env, monkeypatch):
    tmp, exe = env
    monkeypatch.setenv("PATH", str(exe.parent))
    if os.name != "nt":
        exe.chmod(0o755)
    assert vlm_server.find_executable() == Path(str(exe))  # PATH
    local = vlm_server.LOCAL_LLAMA_DIR / vlm_server.EXE
    local.parent.mkdir(parents=True); local.write_bytes(b"")
    assert vlm_server.find_executable() == local  # setup-windows.bat이 받은 tools/llama.cpp가 PATH보다 먼저
    monkeypatch.setenv("LLAMA_SERVER", str(exe))
    assert vlm_server.find_executable() == exe  # 환경변수가 가장 먼저
    monkeypatch.setenv("LLAMA_SERVER", str(tmp / "없음.exe"))
    assert vlm_server.find_executable() is None

def test_problems_name_the_missing_piece(env, monkeypatch):
    tmp, exe = env
    monkeypatch.setenv("PATH", str(tmp / "empty"))
    with pytest.raises(vlm_server.VlmSetupError, match="llama-server 실행 파일"):
        vlm_server.command()
    monkeypatch.setenv("LLAMA_SERVER", str(exe))
    with pytest.raises(vlm_server.VlmSetupError, match="setup-windows.bat.*Qwen/Qwen3-VL-4B-Instruct-GGUF"):
        vlm_server.command()
    _models(tmp / "hub", "8b")
    monkeypatch.setenv("VLM_VARIANT", "8b")
    assert vlm_server.plan().problem() is None

def test_port_of_url():
    assert vlm_server.port_of("http://127.0.0.1:8089") == 8089
    assert vlm_server.port_of("http://127.0.0.1") == vlm_server.DEFAULT_PORT

def test_spawn_writes_log_and_detaches(tmp_path):
    seen = {}
    def popen(cmd, **kw):
        kw["stdout"].write(b"loading model\n")
        seen.update(kw, cmd=cmd)
        return FakeProc()
    log = tmp_path / "out" / "vlm.log"
    proc = vlm_server.spawn(["llama-server"], log=log, popen=popen, cwd=tmp_path)
    assert seen["cmd"] == ["llama-server"] and seen["stderr"] == subprocess.STDOUT and seen["cwd"] == str(tmp_path)
    assert seen.get("start_new_session") is (True if os.name != "nt" else None)
    assert vlm_server.log_tail(log) == "loading model"
    vlm_server.stop(proc)
    assert proc.terminated

def test_default_manager_explains_missing_setup_when_starting(env, monkeypatch):
    tmp, _ = env
    monkeypatch.setenv("PATH", str(tmp / "empty"))
    m = default_vlm_manager("http://127.0.0.1:9")  # 만들 때는 실패하지 않는다(웹앱은 파일 없이도 켜진다)
    with pytest.raises(RuntimeError, match="로컬 AI를 켤 수 없어요: llama-server 실행 파일"):
        m.ensure_ready(timeout=1)

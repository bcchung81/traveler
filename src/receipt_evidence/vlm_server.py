# src/receipt_evidence/vlm_server.py
"""영수증을 읽는 로컬 AI(llama-server) 실행 명령과 프로세스 — macOS·Linux·Windows 공통.
scripts/start_vlm.sh(맥·리눅스 run-app.sh용)와 같은 옵션을 쓴다. Windows에는 bash가 없어 웹앱·CLI는 이 모듈로 직접 띄운다."""
from __future__ import annotations
import os, shutil, subprocess, sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from . import vlm_models

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXE = "llama-server.exe" if os.name == "nt" else "llama-server"
LOCAL_LLAMA_DIR = PROJECT_ROOT / "tools" / "llama.cpp"  # setup-windows.bat이 내려받는 곳
DEFAULT_PORT = 8088

class VlmSetupError(RuntimeError):
    """llama-server 실행 파일이나 모델 파일이 없어 로컬 AI를 켤 수 없음."""

@dataclass(frozen=True)
class LlamaPlan:
    variant: vlm_models.VlmVariant
    executable: Path | None
    model: Path | None
    mmproj: Path | None

    def problem(self) -> str | None:
        v = self.variant
        if self.executable is None:
            return "llama-server 실행 파일이 없어요 — Windows는 setup-windows.bat, 맥은 brew install llama.cpp"
        if not (self.model and self.model.exists() and self.mmproj and self.mmproj.exists()):
            return (f"{v.label} 모델 파일을 찾지 못했어요 — Windows는 setup-windows.bat, "
                    f"또는 hf download {v.repo} {v.model_file} {v.mmproj_file}")
        return None

def find_executable() -> Path | None:
    """LLAMA_SERVER 환경변수 → 저장소 tools/llama.cpp → PATH 순서로 찾는다."""
    if os.environ.get("LLAMA_SERVER"):
        p = Path(os.environ["LLAMA_SERVER"])
        return p if p.is_file() else None
    if (LOCAL_LLAMA_DIR / EXE).is_file():
        return LOCAL_LLAMA_DIR / EXE
    found = shutil.which("llama-server")
    return Path(found) if found else None

def plan() -> LlamaPlan:
    variant = vlm_models.current()
    model, mmproj = variant.paths()
    if os.environ.get("VLM_MODEL"):
        model = Path(os.environ["VLM_MODEL"])
    if os.environ.get("VLM_MMPROJ"):
        mmproj = Path(os.environ["VLM_MMPROJ"])
    return LlamaPlan(variant, find_executable(), model, mmproj)

def port_of(vlm_url: str) -> int:
    return urlsplit(vlm_url).port or DEFAULT_PORT

def command(port: int = DEFAULT_PORT, p: LlamaPlan | None = None) -> list[str]:
    """scripts/start_vlm.sh와 같은 옵션. 파일이 없으면 VlmSetupError."""
    p = p or plan()
    if problem := p.problem():
        raise VlmSetupError(problem)
    parallel = int(os.environ.get("VLM_PARALLEL", "1"))  # receipt-evidence run --workers 와 같은 값
    ctx = int(os.environ.get("VLM_CTX", str(12288 * parallel)))  # 슬롯당 약 12k 토큰
    return [str(p.executable), "-m", str(p.model), "--mmproj", str(p.mmproj), "--host", "127.0.0.1", "--port", str(port),
            "-c", str(ctx), "-ngl", "99", "-np", str(parallel), "--jinja", "--temp", "0", "--alias", "qwen3-vl", "--no-warmup"]

def log_path() -> Path:
    return Path(os.environ.get("VLM_LOG") or PROJECT_ROOT / "out" / "vlm.log")

def spawn(cmd: list[str], log: Path | None = None, popen=subprocess.Popen, cwd: Path = PROJECT_ROOT):
    """로그 파일로 출력을 보내며 llama-server를 띄운다. Windows에서는 이 프로세스가 끝나면(창 닫기 포함) 함께 끝나게 묶는다."""
    log = log or log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "wb") as f:
        kw = {"start_new_session": True} if os.name != "nt" else {}
        proc = popen(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, **kw)
    if os.name == "nt" and hasattr(proc, "_handle"):
        proc._job = _kill_with_parent(proc)
    return proc

def stop(proc, timeout: float = 10.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except Exception:
        proc.kill()

def log_tail(log: Path | None = None, lines: int = 8) -> str:
    try:
        text = (log or log_path()).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])

def _kill_with_parent(proc) -> int | None:
    """Windows Job Object(KILL_ON_JOB_CLOSE)에 넣는다. 핸들은 닫지 않고 두어, 이 프로세스가 끝날 때 운영체제가 닫으며 자식도 끝낸다."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                          "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BasicLimit(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # JobObjectExtendedLimitInformation
            return None
        if not k32.AssignProcessToJobObject(job, int(proc._handle)):
            return None
        return job
    except Exception:  # 묶지 못해도 실행은 계속한다(정상 종료 때는 stop()이 끈다)
        return None

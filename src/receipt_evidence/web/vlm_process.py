"""llama-server를 필요할 때만 띄우고, 이 관리자가 띄운 서버만 끈다(사용자 요청: 필요할 때만 올리기)."""
from __future__ import annotations
import subprocess, time
from collections.abc import Callable
from pathlib import Path

class VlmManager:
    def __init__(self, health: Callable[[], bool], start_cmd: list[str], cwd: Path, popen=subprocess.Popen,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.health = health
        self.start_cmd = list(start_cmd)
        self.cwd = Path(cwd)
        self.popen = popen
        self._sleep = sleep
        self._clock = clock
        self._proc = None

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> str:
        if self.health():
            return "ready"
        return "starting" if self._alive() else "stopped"

    def ensure_ready(self, timeout: float = 180.0, poll: float = 1.0) -> None:
        if self.health():
            return  # 이미 떠 있는 서버(직접 켠 것 포함)는 그대로 쓴다
        if not self._alive():
            self._proc = self.popen(self.start_cmd, cwd=str(self.cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    start_new_session=True)
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            if self._proc is None or self._proc.poll() is not None:
                self._proc = None
                raise RuntimeError("llama-server가 시작 중 종료됨 — out/vlm.log를 확인하세요")
            if self.health():
                return
            self._sleep(poll)
        self.stop()
        raise RuntimeError(f"llama-server가 {int(timeout)}초 안에 준비되지 않음")

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

PROJECT_ROOT = Path(__file__).resolve().parents[3]

def default_vlm_manager(vlm_url: str = "http://127.0.0.1:8088") -> VlmManager:
    """scripts/start_vlm.sh 로 필요할 때만 띄우는 기본 관리자. 상태 확인은 짧은 타임아웃으로."""
    from ..vlm import LlamaServerClient
    health = LlamaServerClient(vlm_url, timeout=2.0).healthy
    return VlmManager(health=health, start_cmd=["bash", str(PROJECT_ROOT / "scripts" / "start_vlm.sh")], cwd=PROJECT_ROOT)

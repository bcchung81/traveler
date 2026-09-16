"""llama-server를 필요할 때만 띄우고, 이 관리자가 띄운 서버만 끈다(사용자 요청: 필요할 때만 올리기)."""
from __future__ import annotations
import subprocess, time
from collections.abc import Callable
from pathlib import Path
from .. import vlm_server

class VlmManager:
    """start_cmd는 명령 목록 또는 켤 때 명령을 만드는 함수(모델·실행 파일이 없으면 그때 사유를 알린다).
    launch를 주면 popen 대신 그것으로 띄운다(로그 파일·Windows 자식 프로세스 정리)."""

    def __init__(self, health: Callable[[], bool], start_cmd: list[str] | Callable[[], list[str]], cwd: Path, popen=subprocess.Popen,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 launch: Callable[[list[str]], object] | None = None):
        self.health = health
        self.start_cmd = start_cmd if callable(start_cmd) else list(start_cmd)
        self.cwd = Path(cwd)
        self.popen = popen
        self.launch = launch
        self._sleep = sleep
        self._clock = clock
        self._proc = None

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> str:
        if self.health():
            return "ready"
        return "starting" if self._alive() else "stopped"

    def _start(self):
        try:
            cmd = self.start_cmd() if callable(self.start_cmd) else self.start_cmd
        except vlm_server.VlmSetupError as e:
            raise RuntimeError(f"로컬 AI를 켤 수 없어요: {e}") from None
        if self.launch is not None:
            return self.launch(cmd)
        return self.popen(cmd, cwd=str(self.cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def ensure_ready(self, timeout: float = 180.0, poll: float = 1.0) -> None:
        if self.health():
            return  # 이미 떠 있는 서버(직접 켠 것 포함)는 그대로 쓴다
        if not self._alive():
            self._proc = self._start()
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
        vlm_server.stop(proc)

PROJECT_ROOT = vlm_server.PROJECT_ROOT

def default_vlm_manager(vlm_url: str = "http://127.0.0.1:8088") -> VlmManager:
    """vlm_url의 포트로 llama-server를 필요할 때만 띄우는 기본 관리자(맥·윈도우 공통). 상태 확인은 짧은 타임아웃으로."""
    from ..vlm import LlamaServerClient
    health = LlamaServerClient(vlm_url, timeout=2.0).healthy
    port = vlm_server.port_of(vlm_url)
    return VlmManager(health=health, start_cmd=lambda: vlm_server.command(port), cwd=PROJECT_ROOT,
                      launch=lambda cmd: vlm_server.spawn(cmd, cwd=PROJECT_ROOT))

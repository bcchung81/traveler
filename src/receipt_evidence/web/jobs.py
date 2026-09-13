"""긴 작업(추출·법령 조회·문서 생성)을 작업 스레드 하나에서 순서대로 실행한다. VLM 슬롯·MCP 세션을 동시에 쓰지 않기 위함."""
from __future__ import annotations
import logging, threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger("receipt_evidence.web")

@dataclass
class Job:
    key: str
    kind: str
    state: str = "queued"  # queued | running | done | error
    message: str = ""
    result: object = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running")

class JobManager:
    def __init__(self, inline: bool = False):
        self.inline = inline
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = None if inline else ThreadPoolExecutor(max_workers=1, thread_name_prefix="receipt-job")

    def _run(self, job: Job, fn: Callable[[], object]) -> None:
        job.state, job.started_at = "running", datetime.now()
        try:
            job.result = fn()
            job.state = "done"
        except Exception as e:  # 작업 실패는 화면에 사유를 보여 주고 서버는 계속 돈다
            log.exception("작업 실패 %s(%s)", job.key, job.kind)
            job.state, job.message = "error", f"{type(e).__name__}: {e}"
        finally:
            job.finished_at = datetime.now()

    def submit(self, key: str, kind: str, fn: Callable[[], object]) -> Job:
        with self._lock:
            current = self._jobs.get(key)
            if current is not None and current.active:
                return current  # 같은 출장에 작업이 이미 돌고 있으면 중복 제출하지 않는다
            job = Job(key=key, kind=kind)
            self._jobs[key] = job
        if self.inline:
            self._run(job, fn)
        else:
            self._pool.submit(self._run, job, fn)
        return job

    def alias(self, key: str, new_key: str) -> None:
        """출장 폴더 이름이 바뀌면 같은 작업을 새 주소에서도 찾게 한다."""
        with self._lock:
            if key in self._jobs:
                self._jobs[new_key] = self._jobs[key]

    def get(self, key: str) -> Job | None:
        with self._lock:
            return self._jobs.get(key)

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)

# src/receipt_evidence/mcp_client.py
from __future__ import annotations
import asyncio, os, threading
from dataclasses import dataclass, field
from typing import Callable, Protocol
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

@dataclass(frozen=True)
class McpResult:
    text: str
    is_error: bool

class ToolCaller(Protocol):
    def call_many(self, calls: list[tuple[str, dict]]) -> list[McpResult]: ...
    def __enter__(self) -> "ToolCaller": ...
    def __exit__(self, *exc) -> None: ...

async def _call_all(session: ClientSession, calls: list[tuple[str, dict]]) -> list[McpResult]:
    out: list[McpResult] = []
    for name, arguments in calls:
        res = await session.call_tool(name, arguments)
        text = "\n".join(c.text for c in (getattr(res, "content", None) or []) if isinstance(c, types.TextContent))
        # mcp 2.x는 is_error, 1.x는 isError — 둘 다 읽어 도구 오류를 성공으로 오인하지 않게 한다
        out.append(McpResult(text=text, is_error=bool(getattr(res, "is_error", None) or getattr(res, "isError", None))))
    return out

class StdioToolCaller:
    """`with` 밖: 호출마다 서버를 띄우는 1회성 세션. `with` 안: 백그라운드 스레드의 이벤트 루프에서 세션 1개를 재사용.
    lazy=True면 `with`에 들어갈 때가 아니라 첫 호출 때 서버를 띄운다(쓰지 않는 작업이 서버 기동 실패에 묶이지 않게)."""

    def __init__(self, command: str, args: list[str], env: dict[str, str] | None = None, timeout: float = 600.0, lazy: bool = False):
        self.command = command
        self.args = list(args)
        self.env = env
        self.timeout = timeout
        self.lazy = lazy
        self._managed = False
        self._start_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None

    def _params(self) -> StdioServerParameters:
        return StdioServerParameters(command=self.command, args=self.args, env=self.env)

    def call_many(self, calls: list[tuple[str, dict]]) -> list[McpResult]:
        if self._loop is None:
            if not (self._managed and self.lazy):
                return asyncio.run(self._oneshot(calls))
            with self._start_lock:
                if self._loop is None:
                    self._start()
        return asyncio.run_coroutine_threadsafe(self._submit(calls), self._loop).result(timeout=self.timeout)

    async def _oneshot(self, calls: list[tuple[str, dict]]) -> list[McpResult]:
        async with stdio_client(self._params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await _call_all(session, calls)

    async def _submit(self, calls: list[tuple[str, dict]]) -> list[McpResult]:
        fut = asyncio.get_running_loop().create_future()
        await self._queue.put((calls, fut))
        return await fut

    async def _serve(self, holder: dict, ready: threading.Event) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        try:
            async with stdio_client(self._params()) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    holder["loop"], holder["queue"] = asyncio.get_running_loop(), queue
                    ready.set()
                    while True:
                        item = await queue.get()
                        if item is None:
                            break
                        calls, fut = item
                        try:
                            fut.set_result(await _call_all(session, calls))
                        except Exception as e:
                            fut.set_exception(e)
        except BaseException as e:  # 세션이 비정상 종료되면 대기 중인 호출을 모두 실패로 끝낸다
            self._error = e
            while not queue.empty():
                item = queue.get_nowait()
                if item is not None and not item[1].done():
                    item[1].set_exception(RuntimeError(f"MCP 세션 종료: {e}"))
        finally:
            ready.set()

    def __enter__(self) -> "StdioToolCaller":
        self._managed = True
        if not self.lazy:
            self._start()
        return self

    def ensure_started(self) -> None:
        """`with` 안에서 서버를 미리 띄운다(패키지 내려받기·기동 확인용)."""
        with self._start_lock:
            if self._loop is None:
                self._start()

    def _start(self) -> None:
        holder: dict = {}
        ready = threading.Event()
        self._error = None
        self._thread = threading.Thread(target=lambda: asyncio.run(self._serve(holder, ready)), daemon=True)
        self._thread.start()
        ready.wait(timeout=180)
        if "loop" not in holder:
            self._thread.join(timeout=5)
            self._thread = None
            raise RuntimeError(f"MCP 서버 시작 실패({self.command} {' '.join(self.args)}): {self._error}")
        self._loop, self._queue = holder["loop"], holder["queue"]

    def __exit__(self, *exc) -> None:
        if self._loop is not None and self._thread is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._queue.put(None), self._loop).result(timeout=10)
            except Exception:
                pass
            self._thread.join(timeout=30)
        self._loop = None
        self._queue = None
        self._thread = None
        self._managed = False

# 버전을 고정해 결과를 재현하고, --prefer-offline으로 받아 둔 패키지를 먼저 쓴다(인터넷이 없어도 기동). 환경변수로 바꿀 수 있다.
KOREAN_LAW_MCP = "korean-law-mcp@4.13.0"
KORDOC_MCP = "kordoc@4.13.1"
LAW_OC_URL = "https://open.law.go.kr"  # 법제처 Open API 인증키(OC)는 각자 무료 발급 — 발급받은 본인만 쓸 수 있어 기본값을 두지 않는다

class LawKeyMissing(RuntimeError):
    """법제처 Open API 인증키(LAW_OC)가 설정되지 않아 법령을 조회할 수 없음."""

def law_oc() -> str:
    return os.environ.get("LAW_OC", "").strip()

class MissingKeyCaller:
    """LAW_OC가 없을 때의 법령 도구. 서버를 띄우지 않고 바로 LawKeyMissing을 낸다(저장해 둔 규정으로 판정이 이어진다)."""

    def __enter__(self) -> "MissingKeyCaller":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def ensure_started(self) -> None:
        return None

    def call_many(self, calls: list[tuple[str, dict]]) -> list[McpResult]:
        raise LawKeyMissing(f"법제처 Open API 인증키(LAW_OC)가 설정되지 않았어요 — {LAW_OC_URL} 에서 무료로 발급받아 LAW_OC 환경변수로 지정하세요")

def law_caller() -> StdioToolCaller | MissingKeyCaller:
    oc = law_oc()
    if not oc:
        return MissingKeyCaller()
    return StdioToolCaller("npx", ["-y", "--prefer-offline", os.environ.get("KOREAN_LAW_MCP", KOREAN_LAW_MCP)], {"LAW_OC": oc}, lazy=True)

def kordoc_caller() -> StdioToolCaller:
    return StdioToolCaller("npx", ["-y", "--prefer-offline", os.environ.get("KORDOC_MCP", KORDOC_MCP), "mcp"], None, lazy=True)

@dataclass
class FakeToolCaller:
    handlers: dict[str, Callable[[dict], str]]
    calls: list[tuple[str, dict]] = field(default_factory=list)

    def __enter__(self) -> "FakeToolCaller":
        return self

    def ensure_started(self) -> None:
        return None

    def __exit__(self, *exc) -> None:
        return None

    def call_many(self, calls: list[tuple[str, dict]]) -> list[McpResult]:
        out = []
        for name, args in calls:
            self.calls.append((name, args))
            h = self.handlers.get(name)
            out.append(McpResult(text=h(args), is_error=False) if h else McpResult(text=f"unknown tool {name}", is_error=True))
        return out

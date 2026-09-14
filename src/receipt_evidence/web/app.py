"""여비정산 증빙 웹앱(C안). 로컬 1인용 — 루프백 Host만 받고, 다른 출처의 상태 변경 요청은 거부한다."""
from __future__ import annotations
from collections.abc import Callable
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from ..mcp_client import kordoc_caller, law_caller
from ..pipeline import Clients
from ..vlm import LlamaServerClient
from ..workspace import NotFound
from .jobs import JobManager
from .service import InvalidName, TripMoved, TripService
from .vlm_process import VlmManager, default_vlm_manager

WEB_DIR = Path(__file__).parent

@dataclass
class WebSettings:
    data_dir: Path
    out_dir: Path
    host: str = "127.0.0.1"
    port: int = 8780
    vlm_url: str = "http://127.0.0.1:8088"
    allowed_hosts: list[str] = field(default_factory=lambda: ["127.0.0.1", "localhost"])

@dataclass
class WebDeps:
    service: TripService
    jobs: JobManager
    vlm: VlmManager
    clients: Callable[[], AbstractContextManager[Clients]]

def default_deps(settings: WebSettings) -> WebDeps:
    @contextmanager
    def clients():
        with law_caller() as law, kordoc_caller() as doc:
            yield Clients(vlm=LlamaServerClient(settings.vlm_url), law=law, doc=doc)
    return WebDeps(service=TripService(settings.data_dir, settings.out_dir), jobs=JobManager(),
                   vlm=default_vlm_manager(settings.vlm_url), clients=clients)

def trip_base(traveler: str, trip_id: str) -> str:
    return f"/t/{quote(traveler, safe='')}/{quote(trip_id, safe='')}"

def see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)

def wants_json(request: Request) -> bool:
    """화면의 스크립트가 fetch로 보낸 요청(Accept: application/json) — 오류를 페이지 대신 팝업에 띄울 JSON으로 돌려준다."""
    return "application/json" in request.headers.get("accept", "")

def _back_link(request: Request) -> str:
    """같은 사이트 안의 이전 화면으로만 돌아간다(다른 사이트 주소는 무시)."""
    ref = request.headers.get("referer", "")
    host = request.headers.get("host", "")
    for prefix in (f"http://{host}", f"https://{host}"):
        if host and ref.startswith(prefix + "/"):
            return ref[len(prefix):]
    return "/"

def _friendly(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):  # pydantic ValidationError: 첫 항목만 사람이 읽는 말로
        try:
            e = errors()[0]
            return f"{'.'.join(str(x) for x in e.get('loc', ()))}: {e.get('msg', '')}".strip(": ")
        except Exception:
            pass
    text = str(exc)
    if "isoformat" in text or "does not match format" in text:
        return f"날짜는 YYYY-MM-DD 형식으로 입력해 주세요 ({text})"
    return text

def create_app(settings: WebSettings, deps: WebDeps | None = None) -> FastAPI:
    deps = deps or default_deps(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        deps.jobs.shutdown()
        deps.vlm.stop()

    app = FastAPI(title="여비정산 증빙", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.deps = settings, deps
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.headers.get('host', '')}":
                return PlainTextResponse("다른 사이트에서 보낸 요청은 받지 않아요", status_code=403)
        return await call_next(request)

    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

    def error_page(request: Request, status_code: int, title: str, message: str) -> HTMLResponse:
        return templates.TemplateResponse(request, "error.html", {"title": title, "message": message, "back": _back_link(request),
                                                                   "vlm_status": deps.vlm.status()}, status_code=status_code)

    @app.exception_handler(InvalidName)
    @app.exception_handler(ValueError)
    async def bad_value(request: Request, exc: ValueError):
        if wants_json(request):
            return JSONResponse({"ok": False, "error": _friendly(exc)}, status_code=400)
        return error_page(request, 400, "입력한 값을 확인해 주세요", _friendly(exc))

    @app.exception_handler(TripMoved)
    async def trip_moved(request: Request, exc: TripMoved):
        """읽은 뒤 출장 폴더 이름이 바뀐 경우: 옛 주소를 새 주소로. htmx 폴링은 보고 있던 화면 주소를 바꿔 HX-Redirect."""
        old, new = f"/t/{exc.traveler}/{exc.old}", trip_base(exc.traveler, exc.new)
        swap = lambda path: new + path[len(old):] if path.startswith(old) else new + "/extract"
        if request.headers.get("hx-request"):
            current = urlsplit(request.headers.get("hx-current-url", ""))
            target = swap(unquote(current.path)) + (f"?{current.query}" if current.query else "")
            return HTMLResponse("", headers={"HX-Redirect": quote(unquote(target), safe="/?=&")})
        target = swap(request.url.path) + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(target, status_code=303 if request.method in ("GET", "HEAD") else 307)

    # 사용자가 가리킨 대상이 없을 때만 404. 코드 버그로 난 KeyError 등은 가리지 않는다
    @app.exception_handler(FileNotFoundError)
    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc: Exception):
        if wants_json(request):
            return JSONResponse({"ok": False, "error": f"찾을 수 없어요: {exc}"}, status_code=404)
        return error_page(request, 404, "찾을 수 없어요", str(exc))

    templates.env.filters["won"] = lambda n: "—" if n is None else f"{int(n):,}"
    templates.env.filters["kdate"] = lambda d: "미정" if not d else f"{d.year}. {d.month}. {d.day}."
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    app.state.templates = templates

    def render(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
        ctx.setdefault("vlm_status", deps.vlm.status())
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)
    app.state.render = render

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, trashed: str = "", restored: str = "", purged: str = ""):
        trips = deps.service.list_trips()
        groups: dict[str, list] = {}
        for t in trips:
            groups.setdefault(t.traveler, []).append(t)
        trash = deps.service.trash_entries()
        just_trashed = next((e for e in trash if e["id"] == trashed), None) if trashed else None
        return render(request, "home.html", trips=trips, groups=list(groups.items()), notices=deps.service.law_notices(date.today()),
                      trash_count=len(trash), just_trashed=just_trashed, restored=restored)

    @app.get("/trash", response_class=HTMLResponse)
    def trash_page(request: Request, purged: str = ""):
        return render(request, "trash.html", entries=deps.service.trash_entries(), purged=bool(purged))

    @app.post("/trash/{trash_id}/restore")
    def restore(trash_id: str):
        traveler, trip_id = deps.service.restore_trash(trash_id)
        return see_other(f"/?restored={quote(f'{traveler}/{trip_id}', safe='')}")

    @app.post("/trash/{trash_id}/purge")
    def purge(trash_id: str):
        deps.service.purge_trash(trash_id)
        return see_other("/trash?purged=1")

    @app.get("/vlm", response_class=HTMLResponse)
    def vlm_badge(request: Request):
        return render(request, "_vlm.html")

    from .routes import register, register_extract, register_result, register_review
    for reg in (register, register_extract, register_review, register_result):
        reg(app, settings, deps, render, trip_base, see_other)
    return app

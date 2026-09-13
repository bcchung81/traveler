"""여비정산 증빙 웹앱(C안). 로컬 1인용 — 루프백 Host만 받고, 다른 출처의 상태 변경 요청은 거부한다."""
from __future__ import annotations
from collections.abc import Callable
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from ..mcp_client import kordoc_caller, law_caller
from ..pipeline import Clients
from ..vlm import LlamaServerClient
from .jobs import JobManager
from .service import InvalidName, TripService
from .vlm_process import VlmManager, default_vlm_manager

WEB_DIR = Path(__file__).parent

@dataclass
class WebSettings:
    data_dir: Path
    out_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765
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

    @app.exception_handler(InvalidName)
    async def invalid_name(request: Request, exc: InvalidName):
        return PlainTextResponse(str(exc), status_code=400)

    @app.exception_handler(ValueError)
    async def bad_value(request: Request, exc: ValueError):
        return PlainTextResponse(str(exc), status_code=400)

    @app.exception_handler(FileNotFoundError)
    @app.exception_handler(KeyError)
    @app.exception_handler(LookupError)
    async def not_found(request: Request, exc: Exception):
        return PlainTextResponse(f"찾을 수 없어요: {exc}", status_code=404)

    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
    templates.env.filters["won"] = lambda n: "—" if n is None else f"{int(n):,}"
    templates.env.filters["kdate"] = lambda d: "미정" if not d else f"{d.year}. {d.month}. {d.day}."
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    app.state.templates = templates

    def render(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
        ctx.setdefault("vlm_status", deps.vlm.status())
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)
    app.state.render = render

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        trips = deps.service.list_trips()
        groups: dict[str, list] = {}
        for t in trips:
            groups.setdefault(t.traveler, []).append(t)
        return render(request, "home.html", trips=trips, groups=list(groups.items()))

    @app.get("/vlm", response_class=HTMLResponse)
    def vlm_badge(request: Request):
        return render(request, "_vlm.html")

    from .routes import register, register_extract
    register(app, settings, deps, render, trip_base, see_other)
    register_extract(app, settings, deps, render, trip_base, see_other)
    return app

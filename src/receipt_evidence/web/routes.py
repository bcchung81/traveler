"""출장 화면 라우트: 1 올리기 → 2 읽은 값 확인 → 3 판정 검토 → 4 서류 완성."""
from __future__ import annotations
from datetime import date
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import UploadFile
from ..ingest import SUPPORTED
from ..workspace import TRIP_YAML_FIELDS
from . import actions
from .service import PROFILE_FIELDS, TripSummary, _name

ERRORS = {"nofiles": "영수증을 먼저 올려 주세요."}

def job_key(traveler: str, trip_id: str) -> str:
    return f"{traveler}/{trip_id}"

def done_steps(status: TripSummary | None) -> tuple[int, ...]:
    if status is None:
        return ()
    done = []
    if status.files:
        done.append(1)
    if status.extracted and not status.stale:
        done.append(2)
    if status.stage == "documented":
        done += [3, 4]
    return tuple(done)

async def _uploads(form) -> list[tuple[str, bytes]]:
    return [(f.filename, await f.read()) for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename]

def _pick(form, keys) -> dict:
    return {k: form[k] for k in keys if k in form}

def register(app: FastAPI, settings, deps, render, trip_base, see_other) -> None:
    service, jobs = deps.service, deps.jobs

    def run_with_clients(fn):
        with deps.clients() as clients:
            return fn(clients)

    def existing(traveler: str, trip_id: str) -> TripSummary:
        status = service.trip_status(traveler, trip_id)
        if not service.trip_dir(status.traveler, status.trip_id).is_dir():
            raise FileNotFoundError(f"{status.traveler}/{status.trip_id}")
        return status

    # ---- 새 정산 ----
    @app.get("/new", response_class=HTMLResponse)
    def new_page(request: Request):
        return render(request, "upload.html", mode="new", base=None, status=None, done=(), files=[], error=None)

    @app.post("/new")
    async def new_create(request: Request):
        form = await request.form()
        traveler, start, dest = (str(form.get(k, "")).strip() for k in ("traveler", "start_date", "destination_region"))
        if not (traveler and start and dest):
            raise ValueError("출장자·출장 시작일·출장지는 꼭 입력해 주세요")
        uploads = await _uploads(form)
        for name, _ in uploads:  # 폴더를 만들기 전에 확장자부터 확인
            if Path(name).suffix.lower() not in SUPPORTED:
                raise ValueError(f"{name}: 지원하지 않는 확장자예요(jpg·jpeg·png·pdf)")
        t, trip_id = service.create_trip(traveler, date.fromisoformat(start), dest)
        service.save_profile(t, _pick(form, PROFILE_FIELDS))
        service.save_trip_yaml(t, trip_id, _pick(form, TRIP_YAML_FIELDS))
        if uploads:
            service.save_files(t, trip_id, uploads)
        return see_other(f"{trip_base(t, trip_id)}/upload")

    # ---- 1 올리기 ----
    @app.get("/t/{traveler}/{trip_id}/upload", response_class=HTMLResponse)
    def upload_page(request: Request, traveler: str, trip_id: str, error: str = ""):
        status = existing(traveler, trip_id)
        t, trip = status.traveler, status.trip_id
        return render(request, "upload.html", mode="edit", base=trip_base(t, trip), status=status, done=done_steps(status),
                      files=service.list_files(t, trip), profile=service.load_profile(t), trip=service.load_trip_yaml(t, trip),
                      error=ERRORS.get(error))

    @app.post("/t/{traveler}/{trip_id}/files")
    async def add_files(request: Request, traveler: str, trip_id: str):
        status = existing(traveler, trip_id)
        service.save_files(status.traveler, status.trip_id, await _uploads(await request.form()))
        return see_other(f"{trip_base(status.traveler, status.trip_id)}/upload")

    @app.post("/t/{traveler}/{trip_id}/files/delete")
    async def delete_file(request: Request, traveler: str, trip_id: str):
        status = existing(traveler, trip_id)
        form = await request.form()
        service.delete_file(status.traveler, status.trip_id, str(form.get("name", "")))
        return see_other(f"{trip_base(status.traveler, status.trip_id)}/upload")

    @app.post("/t/{traveler}/{trip_id}/info")
    async def save_info(request: Request, traveler: str, trip_id: str):
        status = existing(traveler, trip_id)
        form = await request.form()
        service.save_profile(status.traveler, _pick(form, PROFILE_FIELDS))
        service.save_trip_yaml(status.traveler, status.trip_id, _pick(form, TRIP_YAML_FIELDS))
        return see_other(f"{trip_base(status.traveler, status.trip_id)}/upload")

    @app.post("/t/{traveler}/{trip_id}/extract")
    def start_extract(traveler: str, trip_id: str):
        status = existing(traveler, trip_id)
        t, trip = status.traveler, status.trip_id
        base = trip_base(t, trip)
        if not status.files:
            return see_other(f"{base}/upload?error=nofiles")
        jobs.submit(job_key(t, trip), "extract",
                    lambda: run_with_clients(lambda c: actions.do_extract(settings, c, deps.vlm, t, trip)))
        return see_other(f"{base}/extract")

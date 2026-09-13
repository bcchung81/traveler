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

def existing_trip(service, traveler: str, trip_id: str) -> TripSummary:
    """이름을 검증하고(잘못되면 400) 출장 폴더가 없으면 404."""
    status = service.trip_status(traveler, trip_id)
    if not service.trip_dir(status.traveler, status.trip_id).is_dir():
        raise FileNotFoundError(f"{status.traveler}/{status.trip_id}")
    return status

async def _uploads(form) -> list[tuple[str, bytes]]:
    return [(f.filename, await f.read()) for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename]

def _pick(form, keys) -> dict:
    return {k: form[k] for k in keys if k in form}

def register(app: FastAPI, settings, deps, render, trip_base, see_other) -> None:
    service, jobs = deps.service, deps.jobs

    def run_with_clients(fn):
        with deps.clients() as clients:
            return fn(clients)

    existing = lambda traveler, trip_id: existing_trip(service, traveler, trip_id)

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

# ---- 2 읽은 값 확인 ----
JOB_TEXT = {
    "extract": ("영수증을 읽는 중이에요", "새 영수증이 있으면 로컬 AI를 켜서 읽고, 다 읽으면 꺼요. 한 장에 20~40초쯤 걸려요."),
    "law": ("여비 규정을 찾는 중이에요", "공무원 여비 규정 현행본을 조회해요. 하루에 한 번만 조회해요."),
    "finalize": ("HWPX 서류를 만드는 중이에요", "판정 결과로 증빙내역서를 만들고, 다시 읽어 합계를 맞춰 봐요."),
}

def register_extract(app: FastAPI, settings, deps, render, trip_base, see_other) -> None:
    from urllib.parse import quote
    from fastapi.responses import FileResponse
    from ..models import Category
    from ..rules import CROSS_CODES, WARNING_TEXT
    from ..validate import ERROR_CODES
    from ..workspace import load_overrides
    from .service import EDITABLE_RECEIPT_FIELDS
    service, jobs = deps.service, deps.jobs

    existing = lambda traveler, trip_id: existing_trip(service, traveler, trip_id)

    def job_ctx(job):
        title, hint = JOB_TEXT.get(job.kind, ("작업 중이에요", "")) if job else ("", "")
        return {"job": job, "job_title": title, "job_hint": hint}

    @app.get("/t/{traveler}/{trip_id}/extract", response_class=HTMLResponse)
    def extract_page(request: Request, traveler: str, trip_id: str, rid: str = ""):
        status = existing(traveler, trip_id)
        t, trip = status.traveler, status.trip_id
        base = trip_base(t, trip)
        job = jobs.get(job_key(t, trip))
        receipts = service.receipts(t, trip)
        failed = job is not None and job.state == "error" and job.kind == "extract"
        if not receipts and not failed and not (job and job.active):
            return see_other(f"{base}/upload")
        ctx = dict(base=base, status=status, done=done_steps(status), receipts=receipts, **job_ctx(job))
        if receipts:
            selected = next((r for r in receipts if r.receipt_id == rid), receipts[0])
            override = load_overrides(service.trip_dir(t, trip)).get(selected.receipt_id, {})
            ctx.update(selected=selected, images=service.images_for(t, trip).get(selected.receipt_id, []),
                       overridden={k for k in override if k != "clear_warnings"}, cleared=list(override.get("clear_warnings", [])),
                       categories=[c.value for c in Category], warning_text=WARNING_TEXT, blocking_codes=sorted(ERROR_CODES | CROSS_CODES))
        return render(request, "extract.html", **ctx)

    @app.get("/t/{traveler}/{trip_id}/job", response_class=HTMLResponse)
    def job_status(request: Request, traveler: str, trip_id: str):
        status = existing(traveler, trip_id)
        job = jobs.get(job_key(status.traveler, status.trip_id))
        if job is not None and job.active:
            return render(request, "_job.html", base=trip_base(status.traveler, status.trip_id), **job_ctx(job))
        return HTMLResponse("", headers={"HX-Refresh": "true"})  # 끝났으면 화면 전체를 새로 그린다

    @app.post("/t/{traveler}/{trip_id}/receipts/{rid}")
    async def save_receipt(request: Request, traveler: str, trip_id: str, rid: str):
        status = existing(traveler, trip_id)
        t, trip = status.traveler, status.trip_id
        form = await request.form()
        fields = {k: str(form[k]) for k in EDITABLE_RECEIPT_FIELDS if k in form}
        clear = [str(v) for v in form.getlist("clear_warnings")] if ("clear_warnings" in form or "clear_warnings_present" in form) else None
        service.save_override(t, trip, rid, fields, clear)
        base = trip_base(t, trip)
        if form.get("next") == "review":
            return see_other(f"{base}/review")
        return see_other(f"{base}/extract?rid={quote(rid, safe='')}")

    @app.get("/t/{traveler}/{trip_id}/image/{image_id}")
    def receipt_image(traveler: str, trip_id: str, image_id: str):
        status = existing(traveler, trip_id)
        return FileResponse(service.image_path(status.traveler, status.trip_id, image_id), media_type="image/png",
                            headers={"Cache-Control": "private, max-age=600"})

# ---- 3 판정 검토 ----
def resolve_kind(decision, receipt) -> str:
    """확인필요 항목을 화면에서 어떻게 해소할지 고른다."""
    from ..models import Category
    if receipt is None:
        return "period"
    if any("검증 경고" in r for r in decision.reasons):
        return "check"
    if "별표1" in decision.basis:
        return "grade"
    if receipt.category is Category.TAXI:
        return "taxi"
    if receipt.category is Category.LODGING:
        return "lodging"
    return "check"

def register_review(app: FastAPI, settings, deps, render, trip_base, see_other) -> None:
    from ..pipeline import find_job, review_receipts
    from ..report import order_decisions
    service, jobs = deps.service, deps.jobs
    existing = lambda traveler, trip_id: existing_trip(service, traveler, trip_id)

    def run_with_clients(fn):
        with deps.clients() as clients:
            return fn(clients)

    def progress(request, status, job):
        title, hint = JOB_TEXT.get(job.kind, ("작업 중이에요", ""))
        return render(request, "review.html", base=trip_base(status.traveler, status.trip_id), status=status, done=done_steps(status),
                      job=job, job_title=title, job_hint=hint, review=None, rows=[], law=None, law_error=None, pay_count=0)

    @app.get("/t/{traveler}/{trip_id}/review", response_class=HTMLResponse)
    def review_page(request: Request, traveler: str, trip_id: str):
        status = existing(traveler, trip_id)
        t, trip = status.traveler, status.trip_id
        base, key = trip_base(t, trip), job_key(t, trip)
        job = jobs.get(key)
        if job is not None and job.active:
            return progress(request, status, job)
        receipts = service.receipts(t, trip)
        if not receipts:
            return see_other(f"{base}/upload")
        law = service.cached_law(date.today())
        if law is None:
            job = jobs.submit(key, "law", lambda: run_with_clients(lambda c: actions.do_warm_law(settings, c)))
            if job.active:
                return progress(request, status, job)
            law = service.cached_law(date.today())
            if law is None:
                return render(request, "review.html", base=base, status=status, done=done_steps(status), job=None, review=None, rows=[],
                              law=None, law_error=job.message or "법령 캐시를 만들지 못했어요", pay_count=0)
        review = review_receipts(find_job(settings.data_dir, t, trip), receipts, law)
        by_id = {r.receipt_id: r for r in review.receipts}
        rows = []
        for d in order_decisions(review.decisions, review.receipts):
            r = by_id.get(d.receipt_id or "")
            if r is None:
                label, day = ("출장일수 정액" if d.claimed_amount == 0 else d.item), "정액"
            else:
                route = f"{r.origin} → {r.destination}" if r.origin and r.destination else ""
                label = " · ".join(x for x in (r.train_no or r.merchant or r.category.value, route) if x)
                when = r.service_date or (r.paid_at.date() if r.paid_at else None)
                day = (f"결제 {when.month}.{when.day}." if not r.service_date else f"{when.month}.{when.day}.") if when else "미상"
            rows.append({"decision": d, "receipt": r, "label": label, "day": day, "kind": resolve_kind(d, r)})
        pay_count = sum(1 for d in review.decisions if d.verdict.value in ("지급", "감액지급"))
        return render(request, "review.html", base=base, status=status, done=done_steps(status), job=None, review=review, rows=rows,
                      law=law, law_error=None, pay_count=pay_count)

    @app.post("/t/{traveler}/{trip_id}/resolve")
    async def resolve(request: Request, traveler: str, trip_id: str):
        status = existing(traveler, trip_id)
        form = await request.form()
        service.save_profile(status.traveler, _pick(form, ("grade",)))
        service.save_trip_yaml(status.traveler, status.trip_id,
                               _pick(form, ("lodging_region", "over_cap_reason", "taxi_reason", "start_date", "end_date", "destination_region")))
        return see_other(f"{trip_base(status.traveler, status.trip_id)}/review")

    @app.post("/t/{traveler}/{trip_id}/finalize")
    def finalize(traveler: str, trip_id: str):
        status = existing(traveler, trip_id)
        t, trip = status.traveler, status.trip_id
        jobs.submit(job_key(t, trip), "finalize", lambda: run_with_clients(lambda c: actions.do_finalize(settings, c, deps.vlm, t, trip)))
        return see_other(f"{trip_base(t, trip)}/result")

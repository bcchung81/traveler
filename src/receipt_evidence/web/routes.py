"""출장 화면 라우트: 1 올리기 → 2 읽은 값 확인 → 3 판정 검토 → 4 서류 완성."""
from __future__ import annotations
from datetime import date
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import UploadFile
from ..models import Category
from ..stations import city_name
from ..workspace import TRIP_YAML_FIELDS
from . import actions
from .service import PROFILE_FIELDS, TripMoved, TripSummary, _name

ERRORS = {"nofiles": "영수증을 먼저 올려 주세요.", "stale": "파일이 바뀌었어요 — 다시 읽은 뒤에 서류를 만들 수 있어요."}

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
    """이름을 검증하고(잘못되면 400) 출장 폴더가 없으면 404. 읽은 뒤 이름이 바뀐 출장이면 새 주소로 보낸다."""
    status = service.trip_status(traveler, trip_id)
    if not service.trip_dir(status.traveler, status.trip_id).is_dir():
        moved = service.moved_to(status.traveler, status.trip_id)
        if moved:
            raise TripMoved(status.traveler, status.trip_id, moved)
        raise FileNotFoundError(f"{status.traveler}/{status.trip_id}")
    return status

def submit_extract(settings, deps, run_with_clients, t: str, trip: str):
    """영수증 읽기 작업. 끝나면 출장 폴더 이름이 바뀔 수 있어 새 주소에도 작업을 연결한다."""
    def work():
        new_id = run_with_clients(lambda c: actions.do_extract(settings, c, deps.vlm, t, trip, deps.service))
        if new_id != trip:
            deps.jobs.alias(job_key(t, trip), job_key(t, new_id))
        return new_id
    return deps.jobs.submit(job_key(t, trip), "extract", work)

def current_trip_id(job, trip: str) -> str:
    return job.result if job.state == "done" and isinstance(job.result, str) else trip

async def _uploads(form) -> list[tuple[str, bytes]]:
    return [(f.filename, await f.read()) for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename]

def _blank_value(v) -> bool:
    return v is None or v == "" or v == []

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
        return render(request, "upload.html", mode="new", base=None, status=None, done=(), files=[], error=None, travelers=service.travelers())

    @app.post("/new")
    async def new_create(request: Request):
        """첫 화면: 출장자와 영수증만 받는다. 저장하자마자 읽기를 시작하고, 출장 정보는 읽은 값으로 채운다."""
        form = await request.form()
        traveler = str(form.get("traveler_new", "")).strip() or str(form.get("traveler", "")).strip()
        if not traveler:
            raise ValueError("출장자를 고르거나 이름을 적어 주세요")
        t, trip = service.create_staging_trip(traveler, await _uploads(form))
        job = submit_extract(settings, deps, run_with_clients, t, trip)
        return see_other(f"{trip_base(t, current_trip_id(job, trip))}/extract")

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
        t, trip = status.traveler, status.trip_id
        if service.save_files(t, trip, await _uploads(await request.form())):
            job = submit_extract(settings, deps, run_with_clients, t, trip)  # 올리면 바로 읽는다(이미 읽은 영수증은 캐시)
            return see_other(f"{trip_base(t, current_trip_id(job, trip))}/extract")
        return see_other(f"{trip_base(t, trip)}/upload")

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
        job = submit_extract(settings, deps, run_with_clients, t, trip)
        return see_other(f"{trip_base(t, current_trip_id(job, trip))}/extract")

# ---- 2 읽은 값 확인 ----
JOB_TEXT = {
    "extract": ("영수증을 읽는 중이에요", "새 영수증이 있으면 로컬 AI를 켜서 읽고, 다 읽으면 꺼요. 한 장에 20~40초쯤 걸려요."),
    "law": ("여비 규정을 찾는 중이에요", "공무원 여비 규정 현행본을 하루에 한 번 조회해요. 인터넷이 없으면 저장해 둔 규정을 써요."),
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

    def trip_card(t: str, trip: str, receipts) -> dict:
        """출장 정보 카드: 확정 전에는 영수증으로 채운 값(근거 포함), 확정 후에는 저장한 값."""
        if not receipts:
            return {}
        saved = service.load_trip_yaml(t, trip)
        confirmed = (service.trip_dir(t, trip) / "trip.yaml").exists()
        profile = service.load_profile(t)
        sug = service.trip_suggestion(t, trip)
        def field(key, saved_value):
            if confirmed or not _blank_value(saved_value):
                return {"value": saved_value, "auto": False, "basis": "", "guessed": False}
            s = sug.get(key)
            return {"value": s.value if s else None, "auto": bool(s), "basis": s.basis if s else "", "guessed": bool(s and s.guessed)}
        fields = {k: field(k, saved.get(k)) for k in ("start_date", "end_date", "destination_region", "lodging_region", "route_stations")}
        fields["workplace_region"] = field("workplace_region", profile.workplace_region)
        payers = sug["payer_names"].value if "payer_names" in sug else []
        return {"trip_confirmed": confirmed, "trip_fields": fields, "grade": profile.grade or "제2호",
                "has_lodging": any(r.category is Category.LODGING for r in receipts),
                "payer_mismatch": [p for p in payers if p != t]}

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
        ctx = dict(base=base, status=status, done=done_steps(status), receipts=receipts, **job_ctx(job), **trip_card(t, trip, receipts))
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

    @app.post("/t/{traveler}/{trip_id}/trip")
    async def confirm_trip(request: Request, traveler: str, trip_id: str):
        status = existing(traveler, trip_id)
        t, trip = status.traveler, status.trip_id
        form = await request.form()
        new_id = service.confirm_trip(t, trip, {k: str(v) for k, v in form.items() if k != "next"})
        if new_id != trip:
            jobs.alias(job_key(t, trip), job_key(t, new_id))
        return see_other(f"{trip_base(t, new_id)}/{'extract' if form.get('next') == 'extract' else 'review'}")

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

    empty = dict(review=None, rows=[], law=None, law_error=None, law_notes=[], notices=[], pay_count=0, error=None,
                 ask_vehicle=False, ask_in_city=False, doc={}, doc_open=False)

    def progress(request, status, job):
        title, hint = JOB_TEXT.get(job.kind, ("작업 중이에요", ""))
        return render(request, "review.html", **(empty | dict(base=trip_base(status.traveler, status.trip_id), status=status,
                      done=done_steps(status), job=job, job_title=title, job_hint=hint)))

    @app.get("/t/{traveler}/{trip_id}/review", response_class=HTMLResponse)
    def review_page(request: Request, traveler: str, trip_id: str, error: str = ""):
        status = existing(traveler, trip_id)
        t, trip = status.traveler, status.trip_id
        base, key = trip_base(t, trip), job_key(t, trip)
        job = jobs.get(key)
        if job is not None and job.active:
            return progress(request, status, job)
        receipts = service.receipts(t, trip)
        if not receipts:
            return see_other(f"{base}/upload")
        today = date.today()
        book = service.law_book(today)
        if book is None:
            job = jobs.submit(key, "law", lambda: run_with_clients(lambda c: actions.do_warm_law(settings, c)))
            if job.active:
                return progress(request, status, job)
            book = service.law_book(today)
            if book is None:
                return render(request, "review.html", **(empty | dict(base=base, status=status, done=done_steps(status), job=None,
                              law_error=job.message or "여비 규정을 준비하지 못했어요")))
        review = review_receipts(find_job(settings.data_dir, t, trip), receipts, book, service.file_owners())
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
        saved, profile = service.load_trip_yaml(t, trip), service.load_profile(t)
        tr = review.trip
        transport = {Category.RAIL, Category.BUS, Category.AIR, Category.TAXI}
        # 상황별 질문: 판정을 막지 않고, trip.yaml에 답이 생기면 다시 묻지 않는다
        ask_vehicle = (not tr.proposed and not tr.within_workplace and "official_vehicle" not in saved
                       and not any(r.category in transport for r in review.receipts))
        ask_in_city = (not tr.proposed and "within_workplace" not in saved and bool(tr.workplace_region) and bool(tr.destination_region)
                       and city_name(tr.workplace_region) == city_name(tr.destination_region))
        doc = {"purpose": saved.get("purpose") or "", "org": profile.org, "dept": profile.dept, "approval": ", ".join(profile.approval)}
        return render(request, "review.html", base=base, status=status, done=done_steps(status), job=None, review=review, rows=rows,
                      law=review.law, law_error=None, law_notes=review.law_notes, notices=book.notices(today), pay_count=pay_count,
                      error=ERRORS.get(error), ask_vehicle=ask_vehicle, ask_in_city=ask_in_city, doc=doc,
                      doc_open=not (doc["purpose"] and doc["org"] and doc["approval"]))

    @app.post("/t/{traveler}/{trip_id}/resolve")
    async def resolve(request: Request, traveler: str, trip_id: str):
        status = existing(traveler, trip_id)
        form = await request.form()
        service.save_profile(status.traveler, _pick(form, ("grade",)))
        service.save_trip_yaml(status.traveler, status.trip_id,
                               _pick(form, ("lodging_region", "over_cap_reason", "taxi_reason", "start_date", "end_date", "destination_region",
                                            "official_vehicle", "within_workplace", "duration_hours", "purpose")))
        return see_other(f"{trip_base(status.traveler, status.trip_id)}/review")

    def start_finalize(status):
        t, trip = status.traveler, status.trip_id
        if status.stale:  # 새로 올린 영수증은 판정을 검토하지 않았으므로 서류에 넣지 않는다
            return see_other(f"{trip_base(t, trip)}/review?error=stale")
        jobs.submit(job_key(t, trip), "finalize", lambda: run_with_clients(lambda c: actions.do_finalize(settings, c, deps.vlm, t, trip)))
        return see_other(f"{trip_base(t, trip)}/result")

    @app.post("/t/{traveler}/{trip_id}/finalize")
    def finalize(traveler: str, trip_id: str):
        return start_finalize(existing(traveler, trip_id))

    @app.post("/t/{traveler}/{trip_id}/docinfo")
    async def docinfo(request: Request, traveler: str, trip_id: str):
        """서류에 들어갈 정보: 출장 목적(출장별), 기관·부서·결재선(출장자별 기억). next=finalize면 저장 후 바로 서류를 만든다."""
        status = existing(traveler, trip_id)
        form = await request.form()
        service.save_trip_yaml(status.traveler, status.trip_id, _pick(form, ("purpose",)))
        service.save_profile(status.traveler, _pick(form, ("org", "dept", "approval")))
        if form.get("next") == "finalize":
            return start_finalize(status)
        return see_other(f"{trip_base(status.traveler, status.trip_id)}/review")

# ---- 4 서류 완성 ----
PREVIEW_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"

def register_result(app: FastAPI, settings, deps, render, trip_base, see_other) -> None:
    from fastapi.responses import FileResponse, PlainTextResponse
    service, jobs = deps.service, deps.jobs
    existing = lambda traveler, trip_id: existing_trip(service, traveler, trip_id)

    @app.get("/t/{traveler}/{trip_id}/result", response_class=HTMLResponse)
    def result_page(request: Request, traveler: str, trip_id: str):
        status = existing(traveler, trip_id)
        t, trip = status.traveler, status.trip_id
        base = trip_base(t, trip)
        job = jobs.get(job_key(t, trip))
        if job is not None and job.active:
            title, hint = JOB_TEXT.get(job.kind, ("작업 중이에요", ""))
            return render(request, "result.html", base=base, status=status, job=job, job_title=title, job_hint=hint,
                          failed=None, result=None, current=None, versions=[])
        failed = job.message if (job is not None and job.state == "error" and job.kind == "finalize") else None
        result = service.latest_result(t, trip)
        if result is None and not failed:
            return see_other(f"{base}/review")
        versions = service.versions(t, trip)
        return render(request, "result.html", base=base, status=status, job=None, failed=failed, result=result,
                      current=versions[-1] if versions and result else None, versions=list(reversed(versions)))

    @app.get("/t/{traveler}/{trip_id}/v/{version}/evidence.hwpx")
    def download(traveler: str, trip_id: str, version: int):
        status = existing(traveler, trip_id)
        t, trip = status.traveler, status.trip_id
        path = service.version_file(t, trip, version, "evidence.hwpx")
        return FileResponse(path, media_type="application/octet-stream", filename=f"출장여비_증빙내역서_{t}_{trip}_v{version}.hwpx")

    @app.get("/t/{traveler}/{trip_id}/v/{version}/preview", response_class=HTMLResponse)
    def preview(traveler: str, trip_id: str, version: int):
        status = existing(traveler, trip_id)
        html = service.version_file(status.traveler, status.trip_id, version, "preview.html").read_text(encoding="utf-8")
        return HTMLResponse(html, headers={"Content-Security-Policy": PREVIEW_CSP, "X-Content-Type-Options": "nosniff"})

    @app.get("/t/{traveler}/{trip_id}/v/{version}/changes", response_class=PlainTextResponse)
    def changes(traveler: str, trip_id: str, version: int):
        status = existing(traveler, trip_id)
        return PlainTextResponse(service.version_file(status.traveler, status.trip_id, version, "changes.md").read_text(encoding="utf-8"))

# src/receipt_evidence/pipeline.py
from __future__ import annotations
import json, logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from .cache import ExtractCache, cache_key
from .export import export_hwpx, prepare_attachments, verify_hwpx
from .extract import extract_receipts
from .ingest import ingest
from .law import LawBook, get_law_book
from .mcp_client import ToolCaller
from .models import BatchResult, Decision, LawSnapshot, PipelineResult, Receipt, ReceiptImage, TripConfig
from .report import build_markdown, fmt_won, md_table
from .rules import apply_cross_checks, apply_manual_decisions, decide_all, mark_cross_trip_duplicates, review_items, totals
from .validate import validate_all
from .versioning import diff_markdown, fingerprint, load_decisions, plan_version, write_latest
from .vlm import VlmClient
from .workspace import (HashCache, NotFound, TripJob, apply_overrides, clear_warnings, discover, dump_trip_yaml, load_overrides, load_traveler,
                        out_lock, resolve_trip, trip_file_owners, trip_key)

log = logging.getLogger("receipt_evidence")

@dataclass
class Clients:
    vlm: VlmClient
    law: ToolCaller
    doc: ToolCaller

@dataclass
class RunOptions:
    workers: int = 1
    new_version: bool = False
    refresh_law: bool = False
    today: date = field(default_factory=date.today)

@dataclass
class TripWork:
    job: TripJob
    work_dir: Path
    images: list[ReceiptImage]
    receipts: list[Receipt]
    cache_hits: int
    cache_misses: int

@dataclass
class TripReview:
    trip: TripConfig
    receipts: list[Receipt]
    decisions: list[Decision]
    totals: dict[str, int]
    review_items: list[str]
    law: LawSnapshot
    law_notes: list[str] = field(default_factory=list)

def _dump(path: Path, items: list) -> None:
    path.write_text(json.dumps([i.model_dump(mode="json") for i in items], ensure_ascii=False, indent=2), encoding="utf-8")

def _attach_run_log(path: Path) -> None:
    for h in list(log.handlers):
        log.removeHandler(h); h.close()
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(fh); log.setLevel(logging.INFO)

def trip_out_dir(out_dir: Path, job: TripJob) -> Path:
    return out_dir / job.traveler / job.trip_id

def hash_cache(out_dir: Path) -> HashCache:
    return HashCache(out_dir / ".cache" / "hashes.json")

def prepare_trip(job: TripJob, images: list[ReceiptImage], out_dir: Path, clients: Clients, cache: ExtractCache, opts: RunOptions) -> TripWork:
    work = trip_out_dir(out_dir, job) / "work"
    hits, misses = cache.hits, cache.misses
    receipts = extract_receipts(clients.vlm, images, work, cache=cache, workers=opts.workers)
    transcripts = {r.receipt_id: Path(r.transcript_path).read_text(encoding="utf-8") for r in receipts}
    validated = validate_all(receipts, transcripts)
    _dump(work / "receipts.extracted.json", validated)  # overrides 적용 전 원본 — 웹 화면이 overrides.yaml과 즉시 합성
    receipts = apply_overrides(validated, load_overrides(job.trip_dir))
    _dump(work / "receipts.json", receipts)
    log.info("prepare %s/%s: receipts=%d cache_hits=%d cache_misses=%d", job.traveler, job.trip_id, len(receipts),
             cache.hits - hits, cache.misses - misses)
    return TripWork(job, work, images, receipts, cache.hits - hits, cache.misses - misses)

def review_receipts(job: TripJob, receipts: list[Receipt], law: LawBook | LawSnapshot, owners: dict[str, set[str]] | None = None) -> TripReview:
    """파일을 만들지 않고 출장 해석·규정 선택·교차검사·판정·합계만 계산한다(웹 판정 검토 화면과 finalize 공용).
    owners(data/ 전체 파일 해시 → 출장)를 주면 다른 출장에 같은 영수증 파일이 있는지도 본다."""
    trip = resolve_trip(job, load_traveler(job.traveler_dir), receipts)
    snap, notes = law.for_date(trip.start_date) if isinstance(law, LawBook) else (law, [])
    if owners is not None:
        key = trip_key(job.traveler, job.trip_id)
        receipts = mark_cross_trip_duplicates({key: receipts}, owners)[key]
    overrides = load_overrides(job.trip_dir)
    checked = clear_warnings(apply_cross_checks(receipts, trip), overrides)  # 해제한 경고는 교차검사 뒤 마지막에
    decisions = apply_manual_decisions(decide_all(checked, trip, snap), overrides)  # 담당자 판정은 규정 판정 위에
    return TripReview(trip, checked, decisions, totals(decisions), review_items(decisions, checked), snap, notes)

def finalize_trip(work: TripWork, law: LawBook | LawSnapshot, clients: Clients, out_dir: Path, opts: RunOptions, run_id: str,
                  owners: dict[str, set[str]] | None = None) -> PipelineResult:
    job = work.job
    trip_out = trip_out_dir(out_dir, job)
    review = review_receipts(job, work.receipts, law, owners)
    trip, receipts, decisions, t, law = review.trip, review.receipts, review.decisions, review.totals, review.law
    if trip.proposed:
        (work.work_dir / "trip.proposed.yaml").write_text(dump_trip_yaml(trip), encoding="utf-8")
    fp = fingerprint(receipts, trip, law, manual=[(d.receipt_id, d.verdict.value, d.approved_amount, d.manual.reason) for d in decisions if d.manual])
    version, create = plan_version(trip_out, fp, force=opts.new_version)
    vdir = trip_out / f"v{version}"
    common = dict(run_id=run_id, traveler=job.traveler, trip_id=job.trip_id, trip=trip, law_mst=law.mst, law_effective=law.effective,
                  receipts=receipts, decisions=decisions, totals=t, review_items=review.review_items, version=version,
                  cache_hits=work.cache_hits, cache_misses=work.cache_misses, law_notes=review.law_notes)
    if not create:
        verify = json.loads((vdir / "verify.json").read_text(encoding="utf-8")) if (vdir / "verify.json").exists() else {}
        log.info("finalize %s/%s: 변경 없음 v%d", job.traveler, job.trip_id, version)
        return PipelineResult(**common, report_md_path=str(vdir / "report.md"), hwpx_path=str(vdir / "evidence.hwpx"), skipped=True,
                              verify_ok=verify.get("ok"))
    vdir.mkdir(parents=True, exist_ok=True)
    names = prepare_attachments(work.images, receipts, vdir / "attachments")
    md = build_markdown(trip, law, receipts, decisions, names, version=version, law_notes=review.law_notes)
    (vdir / "report.md").write_text(md, encoding="utf-8")
    _dump(vdir / "decisions.json", decisions)
    (vdir / "fingerprint.json").write_text(json.dumps({"fingerprint": fp}, indent=2), encoding="utf-8")
    changes: Path | None = None
    if version > 1:
        changes = vdir / "changes.md"
        changes.write_text(diff_markdown(load_decisions(trip_out / f"v{version - 1}" / "decisions.json"), decisions, version - 1, version), encoding="utf-8")
    hwpx = export_hwpx(clients.doc, md, vdir / "evidence.hwpx", trip, vdir / "attachments")
    verify = verify_hwpx(clients.doc, hwpx, t["approved"])
    (vdir / "verify.json").write_text(json.dumps(verify, ensure_ascii=False, indent=2), encoding="utf-8")
    write_latest(trip_out, version, fp)
    result = PipelineResult(**common, report_md_path=str(vdir / "report.md"), hwpx_path=str(hwpx),
                            changes_md_path=str(changes) if changes else None, verify_ok=bool(verify["ok"]))
    (vdir / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    log.info("finalize %s/%s: v%d approved=%d review=%d verify=%s", job.traveler, job.trip_id, version, t["approved"],
             len(result.review_items), verify["ok"])
    return result

def _failed(job: TripJob, law: LawSnapshot, run_id: str, err: Exception, work: TripWork | None = None) -> PipelineResult:
    return PipelineResult(run_id=run_id, traveler=job.traveler, trip_id=job.trip_id,
                          trip=TripConfig(traveler_name=job.traveler, trip_id=job.trip_id), law_mst=law.mst,
                          law_effective=law.effective, receipts=work.receipts if work else [], decisions=[],
                          totals={"claimed": 0, "approved": 0, "review": 0}, review_items=[], report_md_path="",
                          cache_hits=work.cache_hits if work else 0, cache_misses=work.cache_misses if work else 0,
                          error=f"{type(err).__name__}: {err}")

def write_summary(out_dir: Path, run_id: str, results: list[PipelineResult], warnings: list[str], notices: list[str] | None = None) -> tuple[Path, Path]:
    rows = []
    for r in results:
        state = "오류" if r.error else ("변경 없음" if r.skipped else "새 버전")
        check = "-" if r.verify_ok is None else ("통과" if r.verify_ok else "불일치")
        rows.append([r.traveler, r.trip_id, f"v{r.version}" if r.version else "-", state, f"{len(r.receipts)}건",
                     fmt_won(r.totals.get("claimed", 0)), fmt_won(r.totals.get("approved", 0)), f"{len(r.review_items)}건", check,
                     r.error or r.hwpx_path or "-"])
    md = [f"# 일괄 정산 요약 ({run_id})", "",
          md_table(["출장자", "출장", "버전", "상태", "영수증", "청구", "인정", "확인필요", "합계 검증", "문서 또는 오류"], rows), ""]
    if notices:
        md += ["## 규정 안내", *[f"- {n}" for n in notices], ""]
    if warnings:
        md += ["## 처리하지 않은 파일·폴더", *[f"- {w}" for w in warnings], ""]
    md_path, json_path = out_dir / f"summary-{run_id}.md", out_dir / f"summary-{run_id}.json"
    md_path.write_text("\n".join(md), encoding="utf-8")
    json_path.write_text(json.dumps({"run_id": run_id, "warnings": warnings, "notices": notices or [],
                                     "results": [r.model_dump(mode="json", exclude={"receipts", "decisions"}) for r in results]},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path

def _ensure_vlm(images: list[ReceiptImage], cache: ExtractCache, clients: Clients, on_vlm_needed: Callable[[], None] | None) -> None:
    """새로 읽을 영수증(캐시 미스)이 있을 때만 VLM이 필요하다. 꺼져 있으면 훅(자동 시작)을 부르고 다시 확인한다."""
    if all(cache.has(cache_key(img)) for img in images) or clients.vlm.healthy():
        return
    if on_vlm_needed is not None:
        on_vlm_needed()
        if clients.vlm.healthy():
            return
    raise RuntimeError("llama-server가 응답하지 않음. scripts/start_vlm.sh 로 기동하세요")

def find_job(data_dir: Path, traveler: str, trip_id: str) -> TripJob:
    jobs, _ = discover(data_dir, [traveler], [trip_id])
    if not jobs:
        raise NotFound(f"영수증이 있는 출장 폴더를 찾지 못함: {traveler}/{trip_id}")
    return jobs[0]

def extract_trip(data_dir: Path, out_dir: Path, clients: Clients, traveler: str, trip_id: str, opts: RunOptions | None = None,
                 on_vlm_needed: Callable[[], None] | None = None) -> TripWork:
    """출장 1건만 ingest·추출·검증·overrides 적용(판정·문서 생성은 하지 않음). 규정 조회와 무관하게 끝난다."""
    opts = opts or RunOptions()
    with out_lock(out_dir):
        job = find_job(data_dir, traveler, trip_id)
        cache = ExtractCache(out_dir / ".cache")
        hashes = hash_cache(out_dir)
        images = ingest(job.trip_dir, trip_out_dir(out_dir, job) / "work", sha=hashes.sha256)
        hashes.flush()
        _ensure_vlm(images, cache, clients, on_vlm_needed)
        return prepare_trip(job, images, out_dir, clients, cache, opts)

def run_batch(data_dir: Path, out_dir: Path, clients: Clients, *, travelers: list[str] | None = None, trips: list[str] | None = None,
              opts: RunOptions | None = None, run_id: str | None = None, summary: bool = True,
              on_vlm_needed: Callable[[], None] | None = None) -> BatchResult:
    opts = opts or RunOptions()
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    with out_lock(out_dir):
        _attach_run_log(out_dir / f"run-{run_id}.log")
        jobs, warnings = discover(data_dir, travelers, trips)
        if not jobs:
            raise ValueError("처리할 출장 폴더가 없어요. data/<출장자>/<출장>/ 구조로 영수증을 넣어 주세요")
        # 규정을 먼저 확보한다(조회 실패 시 저장해 둔 규정). 영수증을 다 읽은 뒤에 막히는 일이 없도록.
        book = get_law_book(clients.law, out_dir / ".cache", opts.today, refresh=opts.refresh_law)
        notices = book.notes() + book.notices(opts.today)
        cache, hashes = ExtractCache(out_dir / ".cache"), hash_cache(out_dir)
        outcome: dict[TripJob, PipelineResult] = {}
        ingested: list[tuple[TripJob, list[ReceiptImage]]] = []
        for job in jobs:  # 출장 하나의 실패(깨진 파일 등)가 일괄 처리 전체를 멈추지 않게 한다
            try:
                ingested.append((job, ingest(job.trip_dir, trip_out_dir(out_dir, job) / "work", sha=hashes.sha256)))
            except Exception as e:
                log.exception("ingest 실패 %s/%s", job.traveler, job.trip_id)
                outcome[job] = _failed(job, book.current, run_id, e)
        _ensure_vlm([img for _, imgs in ingested for img in imgs], cache, clients, on_vlm_needed)
        works: list[TripWork] = []
        for job, imgs in ingested:
            try:
                works.append(prepare_trip(job, imgs, out_dir, clients, cache, opts))
            except Exception as e:
                log.exception("prepare 실패 %s/%s", job.traveler, job.trip_id)
                outcome[job] = _failed(job, book.current, run_id, e)
        owners = trip_file_owners(data_dir, hashes)  # data/ 전체 기준 — 출장 1건만 돌려도 다른 출장과의 중복을 본다
        for w in works:
            try:
                outcome[w.job] = finalize_trip(w, book, clients, out_dir, opts, run_id, owners)
            except Exception as e:
                log.exception("finalize 실패 %s/%s", w.job.traveler, w.job.trip_id)
                outcome[w.job] = _failed(w.job, book.current, run_id, e, w)
        results = [outcome[job] for job in jobs]
        if not summary:
            return BatchResult(run_id=run_id, results=results, warnings=warnings, summary_md_path="", summary_json_path="", notices=notices)
        md_path, json_path = write_summary(out_dir, run_id, results, warnings, notices)
        return BatchResult(run_id=run_id, results=results, warnings=warnings, summary_md_path=str(md_path), summary_json_path=str(json_path),
                           notices=notices)

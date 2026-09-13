# src/receipt_evidence/pipeline.py
from __future__ import annotations
import json, logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from .cache import ExtractCache
from .export import export_hwpx, prepare_attachments, verify_hwpx
from .extract import extract_receipts
from .ingest import ingest
from .law import get_law_snapshot
from .mcp_client import ToolCaller
from .models import BatchResult, LawSnapshot, PipelineResult, Receipt, ReceiptImage, TripConfig
from .report import build_markdown, fmt_won, md_table
from .rules import apply_cross_checks, decide_all, mark_cross_trip_duplicates, review_items, totals
from .validate import validate_all
from .versioning import diff_markdown, fingerprint, load_decisions, plan_version, write_latest
from .vlm import VlmClient
from .workspace import TripJob, apply_overrides, discover, dump_trip_yaml, load_overrides, load_traveler, resolve_trip

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

def prepare_trip(job: TripJob, images: list[ReceiptImage], out_dir: Path, clients: Clients, cache: ExtractCache, opts: RunOptions) -> TripWork:
    work = trip_out_dir(out_dir, job) / "work"
    hits, misses = cache.hits, cache.misses
    receipts = extract_receipts(clients.vlm, images, work, cache=cache, workers=opts.workers)
    transcripts = {r.receipt_id: Path(r.transcript_path).read_text(encoding="utf-8") for r in receipts}
    receipts = apply_overrides(validate_all(receipts, transcripts), load_overrides(job.trip_dir))
    _dump(work / "receipts.json", receipts)
    log.info("prepare %s/%s: receipts=%d cache_hits=%d cache_misses=%d", job.traveler, job.trip_id, len(receipts),
             cache.hits - hits, cache.misses - misses)
    return TripWork(job, work, images, receipts, cache.hits - hits, cache.misses - misses)

def finalize_trip(work: TripWork, law: LawSnapshot, clients: Clients, out_dir: Path, opts: RunOptions, run_id: str) -> PipelineResult:
    job = work.job
    trip_out = trip_out_dir(out_dir, job)
    trip = resolve_trip(job, load_traveler(job.traveler_dir), work.receipts)
    if trip.proposed:
        (work.work_dir / "trip.proposed.yaml").write_text(dump_trip_yaml(trip), encoding="utf-8")
    receipts = apply_cross_checks(work.receipts, trip)
    decisions = decide_all(receipts, trip, law)
    t = totals(decisions)
    fp = fingerprint(receipts, trip, law)
    version, create = plan_version(trip_out, fp, force=opts.new_version)
    vdir = trip_out / f"v{version}"
    common = dict(run_id=run_id, traveler=job.traveler, trip_id=job.trip_id, trip=trip, law_mst=law.mst, law_effective=law.effective,
                  receipts=receipts, decisions=decisions, totals=t, review_items=review_items(decisions, receipts), version=version,
                  cache_hits=work.cache_hits, cache_misses=work.cache_misses)
    if not create:
        verify = json.loads((vdir / "verify.json").read_text(encoding="utf-8")) if (vdir / "verify.json").exists() else {}
        log.info("finalize %s/%s: 변경 없음 v%d", job.traveler, job.trip_id, version)
        return PipelineResult(**common, report_md_path=str(vdir / "report.md"), hwpx_path=str(vdir / "evidence.hwpx"), skipped=True,
                              verify_ok=verify.get("ok"))
    vdir.mkdir(parents=True, exist_ok=True)
    names = prepare_attachments(work.images, receipts, vdir / "attachments")
    md = build_markdown(trip, law, receipts, decisions, names, version=version)
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

def _failed(work: TripWork, law: LawSnapshot, run_id: str, err: Exception) -> PipelineResult:
    return PipelineResult(run_id=run_id, traveler=work.job.traveler, trip_id=work.job.trip_id,
                          trip=TripConfig(traveler_name=work.job.traveler, trip_id=work.job.trip_id), law_mst=law.mst,
                          law_effective=law.effective, receipts=work.receipts, decisions=[], totals={"claimed": 0, "approved": 0, "review": 0},
                          review_items=[], report_md_path="", cache_hits=work.cache_hits, cache_misses=work.cache_misses,
                          error=f"{type(err).__name__}: {err}")

def write_summary(out_dir: Path, run_id: str, results: list[PipelineResult], warnings: list[str]) -> tuple[Path, Path]:
    rows = []
    for r in results:
        state = "오류" if r.error else ("변경 없음" if r.skipped else "새 버전")
        check = "-" if r.verify_ok is None else ("통과" if r.verify_ok else "불일치")
        rows.append([r.traveler, r.trip_id, f"v{r.version}" if r.version else "-", state, f"{len(r.receipts)}건",
                     fmt_won(r.totals.get("claimed", 0)), fmt_won(r.totals.get("approved", 0)), f"{len(r.review_items)}건", check,
                     r.error or r.hwpx_path or "-"])
    md = [f"# 일괄 정산 요약 ({run_id})", "",
          md_table(["출장자", "출장", "버전", "상태", "영수증", "청구", "인정", "확인필요", "합계 검증", "문서 또는 오류"], rows), ""]
    if warnings:
        md += ["## 처리하지 않은 파일·폴더", *[f"- {w}" for w in warnings], ""]
    md_path, json_path = out_dir / f"summary-{run_id}.md", out_dir / f"summary-{run_id}.json"
    md_path.write_text("\n".join(md), encoding="utf-8")
    json_path.write_text(json.dumps({"run_id": run_id, "warnings": warnings,
                                     "results": [r.model_dump(mode="json", exclude={"receipts", "decisions"}) for r in results]},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path

def run_batch(data_dir: Path, out_dir: Path, clients: Clients, *, travelers: list[str] | None = None, trips: list[str] | None = None,
              opts: RunOptions | None = None, run_id: str | None = None) -> BatchResult:
    opts = opts or RunOptions()
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    _attach_run_log(out_dir / f"run-{run_id}.log")
    jobs, warnings = discover(data_dir, travelers, trips)
    if not jobs:
        raise ValueError("처리할 출장 폴더가 없어요. data/<출장자>/<출장>/ 구조로 영수증을 넣어 주세요")
    cache = ExtractCache(out_dir / ".cache")
    ingested = [(job, ingest(job.trip_dir, trip_out_dir(out_dir, job) / "work")) for job in jobs]
    if any(not cache.has(img.sha256) for _, imgs in ingested for img in imgs) and not clients.vlm.healthy():
        raise RuntimeError("llama-server가 응답하지 않음. scripts/start_vlm.sh 로 기동하세요")
    works = [prepare_trip(job, imgs, out_dir, clients, cache, opts) for job, imgs in ingested]
    marked = mark_cross_trip_duplicates({str(w.job.trip_dir): w.receipts for w in works})
    for w in works:
        w.receipts = marked[str(w.job.trip_dir)]
    law = get_law_snapshot(clients.law, out_dir / ".cache", opts.today, refresh=opts.refresh_law)
    results: list[PipelineResult] = []
    for w in works:
        try:
            results.append(finalize_trip(w, law, clients, out_dir, opts, run_id))
        except Exception as e:  # 출장 하나의 실패가 일괄 처리 전체를 멈추지 않게 한다
            log.exception("finalize 실패 %s/%s", w.job.traveler, w.job.trip_id)
            results.append(_failed(w, law, run_id, e))
    md_path, json_path = write_summary(out_dir, run_id, results, warnings)
    return BatchResult(run_id=run_id, results=results, warnings=warnings, summary_md_path=str(md_path), summary_json_path=str(json_path))

"""작업 스레드에서 실행하는 본문. VLM은 필요할 때만 켜고, 작업이 끝나면(성공·실패 모두) 이 앱이 켠 서버를 끈다."""
from __future__ import annotations
import os
from datetime import date
from pathlib import Path
from ..law import get_law_snapshot
from ..models import PipelineResult
from ..pipeline import Clients, extract_trip, run_batch
from .vlm_process import VlmManager

def do_extract(settings, clients: Clients, vlm: VlmManager, traveler: str, trip_id: str) -> int:
    try:
        work = extract_trip(settings.data_dir, settings.out_dir, clients, traveler, trip_id, on_vlm_needed=vlm.ensure_ready)
    finally:
        vlm.stop()
    get_law_snapshot(clients.law, settings.out_dir / ".cache", date.today())  # 판정 화면이 바로 열리도록 법령 일자 캐시 예열
    return len(work.receipts)

def do_warm_law(settings, clients: Clients) -> str:
    return get_law_snapshot(clients.law, settings.out_dir / ".cache", date.today()).mst

def do_finalize(settings, clients: Clients, vlm: VlmManager, traveler: str, trip_id: str) -> PipelineResult:
    try:
        batch = run_batch(settings.data_dir, settings.out_dir, clients, travelers=[traveler], trips=[trip_id], summary=False,
                          on_vlm_needed=vlm.ensure_ready)
    finally:
        vlm.stop()
    res = batch.results[0]
    if res.error:
        raise RuntimeError(res.error)
    vdir = Path(res.hwpx_path).parent
    if res.skipped and (vdir / "result.json").exists():
        os.utime(vdir / "result.json")  # 입력이 같아 새 버전이 없어도 '최신 문서' 상태로 보이게
    preview = vdir / "preview.html"
    if not preview.exists():
        try:
            clients.doc.call_many([("render_document", {"file_path": res.hwpx_path, "format": "html", "output_path": str(preview)})])
        except Exception:  # 미리보기는 부가 기능 — 실패해도 문서 생성은 성공으로 둔다
            pass
    return res

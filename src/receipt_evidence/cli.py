# src/receipt_evidence/cli.py
from __future__ import annotations
import argparse, sys
from pathlib import Path
from .mcp_client import kordoc_caller, law_caller
from .pipeline import Clients, RunOptions, run_batch
from .vlm import LlamaServerClient

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="receipt-evidence", description="출장여비 영수증 증빙서류 HWPX 자동화")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="data/<출장자>/<출장>/ 전체를 일괄 처리")
    r.add_argument("--data", default="data", help="데이터 폴더 (기본 data)")
    r.add_argument("--out", default="out", help="출력 폴더 (기본 out)")
    r.add_argument("--traveler", action="append", default=None, help="이 출장자만 (여러 번 지정 가능)")
    r.add_argument("--trip", action="append", default=None, help="이 출장 폴더만 (여러 번 지정 가능)")
    r.add_argument("--workers", type=int, default=1, help="VLM 동시 처리 수 (llama-server VLM_PARALLEL과 맞출 것)")
    r.add_argument("--new-version", action="store_true", help="입력이 같아도 새 버전 문서를 만든다")
    r.add_argument("--refresh-law", action="store_true", help="오늘 법령 캐시를 무시하고 다시 조회")
    r.add_argument("--vlm-url", dest="vlm_url", default="http://127.0.0.1:8088")
    c = sub.add_parser("check-vlm", help="llama-server 상태 확인")
    c.add_argument("--vlm-url", dest="vlm_url", default="http://127.0.0.1:8088")
    return p

def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "check-vlm":
        ok = LlamaServerClient(a.vlm_url).healthy()
        print("llama-server OK" if ok else f"llama-server 응답 없음({a.vlm_url}). scripts/start_vlm.sh 실행 필요")
        return 0 if ok else 2
    opts = RunOptions(workers=a.workers, new_version=a.new_version, refresh_law=a.refresh_law)
    try:
        with law_caller() as law, kordoc_caller() as doc:
            res = run_batch(Path(a.data), Path(a.out), Clients(vlm=LlamaServerClient(a.vlm_url), law=law, doc=doc),
                            travelers=a.traveler, trips=a.trip, opts=opts)
    except Exception as e:  # 배치 전체가 시작조차 못 한 경우
        print(f"실패: {e}", file=sys.stderr)
        return 2
    for r in res.results:
        if r.error:
            print(f"{r.traveler}/{r.trip_id} 오류: {r.error}")
            continue
        state = "변경 없음" if r.skipped else "새 버전"
        print(f"{r.traveler}/{r.trip_id} v{r.version} {state} · 인정 {r.totals['approved']:,}원 · 확인필요 {len(r.review_items)}건 · {r.hwpx_path}")
        for item in r.review_items:
            print("    - " + item)
    for w in res.warnings:
        print("  ! " + w)
    print(f"요약: {res.summary_md_path}")
    if any(r.error for r in res.results):
        return 2
    return 3 if any(r.verify_ok is False for r in res.results) else 0

if __name__ == "__main__":
    sys.exit(main())

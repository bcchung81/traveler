# src/receipt_evidence/cli.py
from __future__ import annotations
import argparse, glob, os, shutil, sys
from datetime import date
from pathlib import Path
from .law import LawUnavailable, get_law_book, kdate
from .mcp_client import kordoc_caller, law_caller
from .pipeline import Clients, RunOptions, run_batch
from .vlm import LlamaServerClient
from .web.vlm_process import VlmManager, default_vlm_manager

LOOPBACK = ("127.0.0.1", "localhost", "::1")

def vlm_manager(vlm_url: str) -> VlmManager:
    """새 영수증을 읽어야 할 때만 llama-server를 켜고, 끝나면 이 명령이 켠 서버를 끈다."""
    return default_vlm_manager(vlm_url)

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
    pr = sub.add_parser("prepare", help="인터넷이 될 때 한 번: 최신 여비 규정 조회·MCP 패키지 내려받기·로컬 AI 준비 확인(오프라인 사용 대비)")
    pr.add_argument("--out", default="out", help="출력 폴더 (기본 out)")
    c = sub.add_parser("check-vlm", help="llama-server 상태 확인")
    c.add_argument("--vlm-url", dest="vlm_url", default="http://127.0.0.1:8088")
    w = sub.add_parser("web", help="로컬 웹앱 실행 (기본 http://127.0.0.1:8780)")
    w.add_argument("--host", default="127.0.0.1", help="이 컴퓨터 주소만 허용 (127.0.0.1 · localhost · ::1)")
    w.add_argument("--port", type=int, default=8780)  # 데모 시연1(tally 8770·기본 8765)과 겹치지 않게
    w.add_argument("--data", default="data")
    w.add_argument("--out", default="out")
    w.add_argument("--vlm-url", dest="vlm_url", default="http://127.0.0.1:8088")
    return p

def vlm_ready() -> tuple[bool, str]:
    """llama-server 실행 파일과 Qwen3-VL 모델 파일이 이 컴퓨터에 있는지(scripts/start_vlm.sh 기본 경로·VLM_MODEL)."""
    if shutil.which("llama-server") is None:
        return False, "llama-server 실행 파일이 없어요(llama.cpp 설치 필요)"
    model = os.environ.get("VLM_MODEL") or next(iter(glob.glob(os.path.expanduser(
        "~/.cache/huggingface/hub/models--Qwen--Qwen3-VL-8B-Instruct-GGUF/snapshots/*/Qwen3VL-8B-Instruct-Q4_K_M.gguf"))), "")
    return (True, f"모델 파일 있음: {model}") if model and Path(model).exists() else (False, "Qwen3-VL 모델 파일을 찾지 못했어요(VLM_MODEL 지정)")

def _prepare(out_dir: Path) -> int:
    ok = True
    with law_caller() as law, kordoc_caller() as doc:
        try:
            book = get_law_book(law, out_dir / ".cache", date.today(), refresh=True)
            state = "최신본 조회함" if book.online else "조회 실패 — 저장해 둔 규정 사용"
            print(f"여비 규정: {state} (MST {book.current.mst}, {kdate(book.current.effective)} 시행)")
            if not book.online:
                ok = False
                print(f"  사유: {book.error}")
            for n in book.notices(date.today()):
                print(f"  ※ {n}")
        except LawUnavailable as e:
            ok = False
            print(f"여비 규정: {e}")
        try:
            doc.ensure_started()
            print("HWPX 도구(kordoc): 준비됨")
        except Exception as e:
            ok = False
            print(f"HWPX 도구(kordoc): 준비 실패 — {e}")
    ready, msg = vlm_ready()
    ok = ok and ready
    print(f"로컬 AI: {msg}")
    print("오프라인 준비 완료 — 인터넷이 없어도 영수증 읽기·판정·서류 만들기를 할 수 있어요" if ok else "일부 준비가 끝나지 않았어요. 인터넷 연결 후 다시 실행하세요")
    return 0 if ok else 2

def _serve_web(settings) -> None:
    import uvicorn
    from .web.app import create_app
    print(f"여비정산 증빙 웹앱: http://{settings.host}:{settings.port}  (종료: Ctrl+C)")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")

def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "check-vlm":
        ok = LlamaServerClient(a.vlm_url).healthy()
        print("llama-server OK" if ok else f"llama-server 응답 없음({a.vlm_url}). scripts/start_vlm.sh 실행 필요")
        return 0 if ok else 2
    if a.cmd == "prepare":
        return _prepare(Path(a.out))
    if a.cmd == "web":
        if a.host not in LOOPBACK:
            print("웹앱은 이 컴퓨터에서만 열 수 있어요(--host 127.0.0.1 · localhost · ::1)", file=sys.stderr)
            return 2
        from .web.app import WebSettings
        _serve_web(WebSettings(data_dir=Path(a.data), out_dir=Path(a.out), host=a.host, port=a.port, vlm_url=a.vlm_url))
        return 0
    opts = RunOptions(workers=a.workers, new_version=a.new_version, refresh_law=a.refresh_law)
    vlm = vlm_manager(a.vlm_url)
    try:
        with law_caller() as law, kordoc_caller() as doc:
            res = run_batch(Path(a.data), Path(a.out), Clients(vlm=LlamaServerClient(a.vlm_url), law=law, doc=doc),
                            travelers=a.traveler, trips=a.trip, opts=opts, on_vlm_needed=vlm.ensure_ready)
    except Exception as e:  # 배치 전체가 시작조차 못 한 경우
        print(f"실패: {e}", file=sys.stderr)
        return 2
    finally:
        vlm.stop()  # 이 명령이 켠 llama-server만 끈다
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
    for n in res.notices:
        print("  ※ " + n)
    print(f"요약: {res.summary_md_path}")
    if any(r.error for r in res.results):
        return 2
    return 3 if any(r.verify_ok is False for r in res.results) else 0

if __name__ == "__main__":
    sys.exit(main())

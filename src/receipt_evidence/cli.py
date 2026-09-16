# src/receipt_evidence/cli.py
from __future__ import annotations
import argparse, logging, os, subprocess, sys, threading, time
from datetime import date
from pathlib import Path
from .law import LawUnavailable, get_law_book, kdate
from . import vlm_server
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
    ap = sub.add_parser("app", help="로컬 AI와 웹앱을 함께 켜고 브라우저를 연다. Ctrl+C나 창 닫기로 둘 다 끈다(Windows run-app.bat)")
    ap.add_argument("--port", type=int, default=8780, help="웹앱 포트 (기본 8780)")
    ap.add_argument("--vlm-port", dest="vlm_port", type=int, default=8088, help="로컬 AI 포트 (기본 8088)")
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="out")
    ap.add_argument("--no-browser", dest="no_browser", action="store_true", help="브라우저를 열지 않는다")
    v = sub.add_parser("vlm", help="로컬 AI(llama-server)를 이 창에서 켠다(Ctrl+C로 끔). 맥·리눅스 scripts/start_vlm.sh와 같은 설정")
    v.add_argument("--port", type=int, default=int(os.environ.get("VLM_PORT") or 8088))
    v.add_argument("--dry-run", dest="dry_run", action="store_true", help="켜지 않고 쓸 실행 파일·모델 경로만 보여 준다(없으면 종료 코드 1)")
    return p

def vlm_ready() -> tuple[bool, str]:
    """llama-server 실행 파일과 운영 모델(VLM_VARIANT, 기본 Qwen3-VL 4B) 파일이 이 컴퓨터에 있는지."""
    plan = vlm_server.plan()
    if problem := plan.problem():
        return False, problem
    return True, f"{plan.variant.label} 모델 파일 있음: {plan.model} (llama-server: {plan.executable})"

def _vlm(a) -> int:
    plan = vlm_server.plan()
    if a.dry_run:  # setup-windows.bat이 모델을 내려받을지 이 출력으로 정한다
        v = plan.variant
        print(f"variant={v.key}\nrepo={v.repo}\nfiles={v.model_file} {v.mmproj_file}")
        print(f"llama_server={plan.executable or ''}\nmodel={plan.model or ''}\nmmproj={plan.mmproj or ''}")
    try:
        cmd = vlm_server.command(a.port, plan)
    except vlm_server.VlmSetupError as e:
        print(f"로컬 AI: {e}", file=sys.stderr)
        return 1
    if a.dry_run:
        return 0
    print(f"로컬 AI: http://127.0.0.1:{a.port} {plan.variant.label} (끄기: Ctrl+C)")
    try:
        return subprocess.run(cmd).returncode
    except KeyboardInterrupt:
        return 0

def _port_busy(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0

def _open_browser_when_ready(url: str, seconds: float = 60.0) -> None:
    import httpx, webbrowser
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            httpx.get(url, timeout=1.0)
            webbrowser.open(url)
            return
        except httpx.HTTPError:
            time.sleep(0.5)

def _app(a) -> int:
    """run-app.sh start의 창 하나짜리 판: 로컬 AI를 켜고 준비되면 웹앱을 켠다. 끝날 때 이 명령이 켠 로컬 AI만 끈다."""
    from .web.app import WebSettings
    web_url, vlm_url = f"http://127.0.0.1:{a.port}", f"http://127.0.0.1:{a.vlm_port}"
    if _port_busy(a.port):
        print(f"웹앱: 포트 {a.port}을(를) 다른 프로그램이 쓰고 있어요. 이미 켜 둔 창이 있으면 {web_url} 을 여세요(다른 번호: --port 8790)", file=sys.stderr)
        return 2
    vlm = default_vlm_manager(vlm_url)
    if vlm.health():
        print(f"로컬 AI: 이미 실행 중 — :{a.vlm_port}")
    elif _port_busy(a.vlm_port):
        print(f"로컬 AI: 포트 {a.vlm_port}을(를) 다른 프로그램이 쓰고 있어요(다른 번호: --vlm-port 8089)", file=sys.stderr)
        return 2
    else:
        wait = float(os.environ.get("VLM_WAIT") or 180)
        print(f"로컬 AI: 켜는 중 — :{a.vlm_port} {vlm_server.plan().variant.label} (모델 로딩 최대 {int(wait)}초)")
        try:
            vlm.ensure_ready(timeout=wait)
        except RuntimeError as e:
            print(f"로컬 AI: {e}", file=sys.stderr)
            if tail := vlm_server.log_tail():
                print(tail, file=sys.stderr)
            return 2
        print("로컬 AI: 준비됨")
    try:
        if not a.no_browser:
            threading.Thread(target=_open_browser_when_ready, args=(web_url,), daemon=True).start()
        print("이 창을 닫거나 Ctrl+C를 누르면 웹앱과 로컬 AI가 함께 꺼져요")
        _serve_web(WebSettings(data_dir=Path(a.data), out_dir=Path(a.out), port=a.port, vlm_url=vlm_url))
    finally:
        vlm.stop()  # 이 명령이 켠 llama-server만 끈다
    return 0

def _prepare(out_dir: Path) -> int:
    logging.getLogger("receipt_evidence").addHandler(logging.NullHandler())  # 결과는 아래에서 직접 알린다(같은 경고를 두 번 찍지 않게)
    ok = True
    with law_caller() as law, kordoc_caller() as doc:
        try:
            book = get_law_book(law, out_dir / ".cache", date.today(), refresh=True)
            state = "최신본 조회함" if book.online else ("법제처 인증키(LAW_OC) 없음 — 저장해 둔 규정 사용" if book.no_key else "조회 실패 — 저장해 둔 규정 사용")
            print(f"여비 규정: {state} (MST {book.current.mst}, {kdate(book.current.effective)} 시행)")
            if not book.online:
                ok = False
                if not book.no_key:
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
    print("오프라인 준비 완료 — 인터넷이 없어도 영수증 읽기·판정·서류 만들기를 할 수 있어요" if ok
          else "일부 준비가 끝나지 않았어요(위 안내 참고). 인터넷 연결·인증키 설정 후 다시 실행하세요")
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
        print("llama-server OK" if ok else f"llama-server 응답 없음({a.vlm_url}). receipt-evidence vlm(맥·리눅스: scripts/start_vlm.sh)으로 켜세요")
        return 0 if ok else 2
    if a.cmd == "prepare":
        return _prepare(Path(a.out))
    if a.cmd == "vlm":
        return _vlm(a)
    if a.cmd == "app":
        return _app(a)
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

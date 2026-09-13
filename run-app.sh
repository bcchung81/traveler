#!/usr/bin/env bash
# 여비정산 증빙 — 로컬 AI(llama-server)와 웹앱을 함께 켜고 끈다.
#   사용: ./run-app.sh start | stop | restart | status
#   웹      http://127.0.0.1:${PORT:-8780}
#   로컬 AI http://127.0.0.1:${VLM_PORT:-8088}   (데모 시연1 실적 취합은 8770)
# 환경변수: PORT, VLM_PORT, OPEN=0(브라우저 안 열기), VLM_WAIT(로컬 AI 준비 대기 초, 기본 180)
set -uo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8780}"
VLM_PORT="${VLM_PORT:-8088}"
VLM_WAIT="${VLM_WAIT:-180}"
RUN_DIR=".run"
WEB_PID="$RUN_DIR/web.pid"
LLAMA_PID="$RUN_DIR/llama.pid"

alive()      { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
port_owner() { { lsof -nP -iTCP:"$1" -sTCP:LISTEN -Fc 2>/dev/null || true; } | sed -n 's/^c//p' | head -1; }
vlm_up()     { curl -sf -o /dev/null --max-time 2 "http://127.0.0.1:${VLM_PORT}/health"; }
web_up()     { curl -sf -o /dev/null --max-time 2 "http://127.0.0.1:${PORT}/"; }

# 이름 · pid 파일 · 포트 · 명령줄에 있어야 할 문자열(다른 프로그램을 잘못 끄지 않도록)
stop_service() {
  local label="$1" pid_file="$2" port="$3" expect="$4" pids="" p left
  if alive "$pid_file"; then pids="$(cat "$pid_file")"; fi
  for p in $({ lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true; }); do
    if ps -o command= -p "$p" 2>/dev/null | grep -q "$expect"; then pids="$pids $p"; fi
  done
  pids="$(echo "$pids" | tr ' ' '\n' | sed '/^$/d' | sort -u | tr '\n' ' ')"
  if [ -z "${pids// /}" ]; then
    echo "${label}: 실행 중이 아님"
    rm -f "$pid_file"
    return 0
  fi
  kill -TERM $pids 2>/dev/null
  for _ in $(seq 1 15); do
    left=""
    for p in $pids; do kill -0 "$p" 2>/dev/null && left="$left $p"; done
    [ -z "$left" ] && break
    sleep 1
  done
  for p in $pids; do kill -0 "$p" 2>/dev/null && kill -KILL "$p" 2>/dev/null; done
  rm -f "$pid_file"
  echo "${label}: 종료함 (pid ${pids% })"
}

start_llama() {
  if vlm_up; then
    echo "로컬 AI: 이미 실행 중 — :${VLM_PORT}"
    return 0
  fi
  local owner; owner="$(port_owner "$VLM_PORT")"
  if [ -n "$owner" ]; then
    echo "로컬 AI: 포트 ${VLM_PORT}을(를) 다른 프로그램(${owner})이 쓰고 있어요. VLM_PORT=다른번호 로 실행하세요" >&2
    return 1
  fi
  echo "로컬 AI: 켜는 중 — :${VLM_PORT} (모델 로딩 최대 ${VLM_WAIT}초)"
  VLM_PORT="$VLM_PORT" nohup bash scripts/start_vlm.sh >/dev/null 2>&1 &
  echo $! > "$LLAMA_PID"
  for _ in $(seq 1 "$VLM_WAIT"); do
    if vlm_up; then echo "로컬 AI: 준비됨"; return 0; fi
    if ! alive "$LLAMA_PID"; then
      echo "로컬 AI: 시작 중 종료됐어요 — out/vlm.log를 확인하세요" >&2
      rm -f "$LLAMA_PID"
      return 1
    fi
    sleep 1
  done
  echo "로컬 AI: ${VLM_WAIT}초 안에 준비되지 않았어요 — out/vlm.log를 확인하세요" >&2
  return 1
}

start_web() {
  if alive "$WEB_PID"; then
    echo "웹앱: 이미 실행 중 — http://127.0.0.1:${PORT}"
    return 0
  fi
  local owner; owner="$(port_owner "$PORT")"
  if [ -n "$owner" ]; then
    echo "웹앱: 포트 ${PORT}을(를) 다른 프로그램(${owner})이 쓰고 있어요. PORT=다른번호 로 실행하세요" >&2
    return 1
  fi
  uv sync --frozen --offline -q 2>/dev/null || uv sync --frozen -q || return 1  # 인터넷이 없어도 설치된 패키지로 켠다
  nohup .venv/bin/receipt-evidence web --port "$PORT" --vlm-url "http://127.0.0.1:${VLM_PORT}" > "$RUN_DIR/web.log" 2>&1 &
  echo $! > "$WEB_PID"
  for _ in $(seq 1 60); do
    if web_up; then echo "웹앱: 준비됨 — http://127.0.0.1:${PORT}"; return 0; fi
    if ! alive "$WEB_PID"; then
      echo "웹앱: 시작 중 종료됐어요 — ${RUN_DIR}/web.log를 확인하세요" >&2
      rm -f "$WEB_PID"
      return 1
    fi
    sleep 1
  done
  echo "웹앱: 60초 안에 응답이 없어요 — ${RUN_DIR}/web.log를 확인하세요" >&2
  return 1
}

cmd_start() {
  mkdir -p "$RUN_DIR" out
  if ! start_llama || ! start_web; then
    echo "켜기에 실패해서 이번에 켠 서비스를 정리합니다." >&2
    cmd_stop
    return 1
  fi
  if [ "${OPEN:-1}" != "0" ] && command -v open >/dev/null; then open "http://127.0.0.1:${PORT}" || true; fi
  echo "끄기: ./run-app.sh stop"
}

cmd_stop() {
  stop_service "웹앱(:${PORT})" "$WEB_PID" "$PORT" "receipt-evidence"
  stop_service "로컬 AI(:${VLM_PORT})" "$LLAMA_PID" "$VLM_PORT" "llama-server"
}

cmd_status() {
  if web_up; then echo "웹앱(:${PORT}): 실행 중 — http://127.0.0.1:${PORT}"; else echo "웹앱(:${PORT}): 꺼짐"; fi
  if vlm_up; then echo "로컬 AI(:${VLM_PORT}): 실행 중"; else echo "로컬 AI(:${VLM_PORT}): 꺼짐"; fi
}

case "${1:-}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status)  cmd_status ;;
  *) echo "사용: ./run-app.sh start | stop | restart | status" >&2; exit 2 ;;
esac

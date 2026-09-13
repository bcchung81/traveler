# receipt-evidence

출장여비 영수증을 로컬 AI(Qwen3-VL)로 읽고 공무원 여비 규정으로 판정해, 출장별 HWPX 증빙내역서를 만듭니다.

## 설치
```bash
uv sync
```
필요: llama.cpp(`llama-server`), Qwen3-VL GGUF(`scripts/start_vlm.sh` 경로), Node.js(`npx`로 kordoc·korean-law-mcp 실행).

## 영수증 넣기
```
data/<출장자>/traveler.yaml                 여비 구분·근무지·결재선 (examples/traveler.yaml)
data/<출장자>/<YYYY-MM-DD_출장지>/trip.yaml   출장기간·출장지·목적 (없으면 자동 제안)
data/<출장자>/<YYYY-MM-DD_출장지>/overrides.yaml  사용자 확인값 (선택)
data/<출장자>/<YYYY-MM-DD_출장지>/*.jpg|png|pdf  영수증
```
위 구조(`data/<출장자>/<출장>/`)로 넣어야 처리됩니다.

## 실행
```bash
# llama-server는 필요할 때 자동으로 켜지고 끝나면 꺼집니다(수동: bash scripts/start_vlm.sh)
uv run receipt-evidence run                 # 전체 일괄
uv run receipt-evidence run --traveler 정백철 --trip 2026-07-09_서울 --workers 4
```
- 다시 실행하면 이미 읽은 영수증은 캐시를 쓰고, 입력이 바뀐 출장만 새 버전(`v2`, `v3` …)을 만듭니다.
- 결과: `out/<출장자>/<출장>/v<N>/evidence.hwpx`, 요약 `out/summary-<run_id>.md`.

## 웹앱 실행
```bash
./run-app.sh start     # 로컬 AI(llama-server :8088)를 켜고, 준비되면 웹앱(:8780)을 켜고 브라우저를 연다
./run-app.sh stop      # 웹앱과 로컬 AI를 함께 끈다
./run-app.sh status    # 켜져 있는지 확인
./run-app.sh restart   # 껐다 켜기
```
포트를 바꾸려면 `PORT=8790 VLM_PORT=8089 ./run-app.sh start` (끌 때도 같은 값). 브라우저를 열지 않으려면 `OPEN=0 ./run-app.sh start`.
실행 기록은 `.run/`(pid·웹 로그), 로컬 AI 로그는 `out/vlm.log`.

스크립트 없이 웹앱만 띄울 수도 있습니다.
```bash
uv run receipt-evidence web                 # http://127.0.0.1:8780 (이 컴퓨터에서만 열림)
```
홈(출장 목록) → 새 정산 → ① 영수증 올리기 → ② 읽은 값 확인 → ③ 판정 검토 → ④ 서류 완성(미리보기·내려받기·버전 이력).
`./run-app.sh start`로 켜면 로컬 AI가 웹앱과 함께 떠 있고 `./run-app.sh stop`으로 함께 꺼집니다. 웹앱만 따로 띄운 경우에는 새 영수증을 읽을 때만 자동으로 켜지고 다 읽으면 꺼집니다(`run` 명령도 같음).

## 테스트
```bash
uv run pytest                    # 단위 테스트 (네트워크·VLM 불필요)
uv run pytest -m integration     # 실제 llama-server·MCP 연동
```

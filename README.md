# receipt-evidence — 출장여비 영수증 증빙 자동화

출장여비 영수증을 **이 컴퓨터 안의 AI(Qwen3-VL)** 로 읽고, 공무원 여비 규정으로 판정해 출장별 **HWPX 증빙내역서**를 만듭니다.
영수증 이미지는 외부 AI 서비스로 보내지 않습니다. 웹앱도 이 컴퓨터(127.0.0.1)에서만 열립니다.

- [Windows에서 시작하기](#windows에서-시작하기) — 설정 파일 더블클릭 → 실행 파일 더블클릭
- [macOS·Linux에서 시작하기](#macoslinux에서-시작하기)
- [사용법](#사용법) · [인터넷 없이 쓰기](#인터넷-없이-쓰기) · [폴더 구조](#폴더-구조) · [테스트](#테스트)
- 이 앱처럼 직접 만들어 보려면 → **[바이브코딩 따라하기](docs/vibe-coding-quickstart.md)** — Claude Code 설치 · MCP · 스킬 (1페이지, 15분)

---

## Windows에서 시작하기

### 준비물

| 항목 | 기준 |
|---|---|
| 운영체제 | Windows 10(1809 이상) 또는 11, 64비트 |
| 디스크 | 여유 공간 약 6GB (AI 모델 2.8GB, 파이썬·Node.js·llama.cpp) |
| 메모리 | 16GB 권장 (그래픽카드 메모리 6GB 이상이면 더 빠름) |
| 그래픽카드 | 없어도 됩니다. NVIDIA는 CUDA, AMD·Intel은 Vulkan으로 자동 선택하고, GPU가 없으면 CPU로 읽습니다(느림) |
| 인터넷 | 처음 설정할 때만 필요합니다 |
| Git | 저장소를 받고 새 버전으로 올릴 때 씁니다 — 아래 1단계에서 설치 |

**Git만 직접 설치**하면, 나머지 **uv(파이썬 3.14 포함) · Node.js · llama.cpp · Qwen3-VL 모델**은 설정 스크립트가 모두 설치합니다.
설치에는 Windows 기본 '앱 설치 관리자'(`winget`)를 씁니다.

### 1. Git 설치하고 저장소 내려받기

**① Git 설치** — 시작 메뉴에서 **PowerShell**을 열고:
```powershell
winget install --id Git.Git -e --source winget
```
- `winget`이 없으면 [git-scm.com/install/windows](https://git-scm.com/install/windows)에서 **Git for Windows/x64 Setup**을 받아 **기본값 그대로 [Next]** 로 설치합니다.
- 설치가 끝나면 **PowerShell을 닫고 새로 연 뒤** `git --version`으로 버전이 나오는지 확인합니다.

**② 저장소 내려받기**
```powershell
cd $HOME                                              # 내 사용자 폴더(C:\Users\이름)에 받기
git clone https://github.com/bcchung81/traveler.git   # traveler 폴더가 생김
cd traveler
explorer .                                            # 탐색기로 폴더 열기 → 다음 단계의 setup-windows.bat
```
- Git을 쓰지 않으려면 [저장소 페이지](https://github.com/bcchung81/traveler)에서 **Code → Download ZIP**으로 받아 압축을 풀어도 됩니다(이 경우 새 버전은 다시 ZIP으로 받아야 함).
- **새 버전 받기:** 저장소 폴더에서 `git pull` → `run-app.bat` 실행(바뀐 파이썬 패키지는 자동으로 맞춥니다).

### 2. 환경 설정 — `setup-windows.bat` 더블클릭

처음에는 10~20분 걸립니다(대부분 모델 내려받기 시간). 다시 실행하면 이미 있는 것은 건너뜁니다.

| 단계 | 하는 일 |
|---|---|
| 1/6 uv | 파이썬·패키지 관리자 [uv](https://docs.astral.sh/uv/) 설치 (관리자 권한 불필요) |
| 2/6 Node.js | Node.js 20.19 이상이 없으면 `winget`으로 LTS 설치 (관리자 권한 창에서 '예') |
| 3/6 파이썬 패키지 | `uv sync --frozen` — 파이썬 3.14와 패키지를 `.venv`에 설치 |
| 4/6 llama.cpp | 로컬 AI 실행기 llama.cpp `b9740`을 `tools\llama.cpp`에 내려받고, GPU 인식 여부를 보여 줌 |
| 5/6 모델 | Qwen3-VL-4B-Instruct(Q4_K_M)와 이미지 인코더(mmproj Q8_0)를 허깅페이스 캐시(`%USERPROFILE%\.cache\huggingface\hub`)에 내려받기 |
| 6/6 점검 | `receipt-evidence prepare` — 최신 여비 규정 조회, HWPX 도구(kordoc)·규정 도구(korean-law-mcp) 내려받기, 전체 점검 |

- "Windows의 PC 보호" 창이 뜨면 **추가 정보 → 실행**을 누릅니다(인터넷에서 받은 .bat 파일이라 뜨는 안내).
- 옵션은 명령 프롬프트에서 붙입니다.
  - `setup-windows.bat -DryRun` — 설치하지 않고 할 일만 보기
  - `setup-windows.bat -Backend vulkan -ReinstallLlama` — llama.cpp 빌드 바꾸기(`auto` · `cuda` · `vulkan` · `cpu`)
  - `setup-windows.bat -SkipModel` — 모델 내려받기 건너뛰기

### 3. 웹앱 실행 — `run-app.bat` 더블클릭

로컬 AI(llama-server, 포트 8088)를 켜고, 준비되면 웹앱(포트 8780)을 켠 뒤 브라우저로 **http://127.0.0.1:8780** 을 엽니다.

- **끄기: 그 검은 창을 닫거나 Ctrl+C** — 웹앱과 로컬 AI가 함께 꺼집니다.
- 모델을 불러오는 데 처음에는 수십 초 걸립니다.
- 옵션:
  - `run-app.bat --port 8790 --vlm-port 8089` — 포트가 이미 쓰이고 있을 때
  - `run-app.bat --no-browser` — 브라우저를 열지 않기

명령 프롬프트나 PowerShell에서 직접 실행하려면(파이썬 환경은 `uv`가 알아서 씁니다):
```powershell
uv run receipt-evidence app                # run-app.bat과 같음
uv run receipt-evidence web                # 웹앱만 (로컬 AI는 새 영수증을 읽을 때만 켜지고 끝나면 꺼짐)
uv run receipt-evidence vlm --dry-run      # 쓸 llama-server·모델 경로 확인
```

### Windows 문제 해결

| 증상 | 해결 |
|---|---|
| `winget이 필요해요` | Microsoft Store에서 '앱 설치 관리자'를 설치하거나, [nodejs.org](https://nodejs.org/ko)에서 Node.js LTS를 직접 설치한 뒤 `setup-windows.bat`을 다시 실행 |
| `llama-server를 실행하지 못했어요` | 스크립트가 Visual C++ 런타임을 설치해 다시 확인합니다. 그래도 안 되면 `setup-windows.bat -Backend cpu -ReinstallLlama` |
| `GPU를 찾지 못해 CPU로 읽어요` / 읽기가 너무 느림 | 그래픽 드라이버를 최신으로 올린 뒤 `setup-windows.bat -ReinstallLlama`. NVIDIA인데 안 되면 `-Backend vulkan`도 시험 |
| `로컬 AI: llama-server가 시작 중 종료됨` | `out\vlm.log` 끝부분 확인. 그래픽카드 메모리가 부족하면 `-Backend cpu -ReinstallLlama` |
| `포트 8780을(를) 다른 프로그램이 쓰고 있어요` | 이미 켜 둔 창이 있는지 확인하거나 `run-app.bat --port 8790` |
| 백신이 `llama-server.exe`를 막음 | 저장소의 `tools\llama.cpp` 폴더를 검사 예외에 추가 |
| 회사망에서 내려받기 실패 | 프록시가 있으면 명령 프롬프트에서 `set HTTPS_PROXY=http://프록시:포트` 후 `setup-windows.bat` 실행 |
| 스크립트 실행 정책 오류 | `.bat` 파일로 실행하세요(실행 정책을 이번 실행에만 우회합니다). `.ps1`을 직접 실행한다면 `powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1` |

환경변수는 명령 프롬프트에서 `set 이름=값`, PowerShell에서 `$env:이름='값'`으로 지정한 뒤 같은 창에서 `run-app.bat`을 실행합니다.
예: 8B 모델로 바꾸기 — `set VLM_VARIANT=8b` 후 `setup-windows.bat`(8B 모델 내려받기) → `run-app.bat`.

---

## macOS·Linux에서 시작하기

```bash
git --version || xcode-select --install   # 맥은 Git이 없으면 설치 창이 뜬다 (또는 brew install git, 리눅스는 apt install git)
git clone https://github.com/bcchung81/traveler.git && cd traveler
uv sync
brew install llama.cpp node          # 리눅스는 llama.cpp 릴리스·패키지와 Node.js 20.19+ 설치
uvx --from huggingface_hub hf download Qwen/Qwen3-VL-4B-Instruct-GGUF Qwen3VL-4B-Instruct-Q4_K_M.gguf mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf
uv run receipt-evidence prepare      # 여비 규정·MCP 패키지·로컬 AI 점검
```

```bash
./run-app.sh start     # 로컬 AI(llama-server :8088)를 켜고, 준비되면 웹앱(:8780)을 켜고 브라우저를 연다
./run-app.sh stop      # 웹앱과 로컬 AI를 함께 끈다
./run-app.sh status    # 켜져 있는지 확인
./run-app.sh restart   # 껐다 켜기
```
포트를 바꾸려면 `PORT=8790 VLM_PORT=8089 ./run-app.sh start` (끌 때도 같은 값). 브라우저를 열지 않으려면 `OPEN=0 ./run-app.sh start`.
실행 기록은 `.run/`(pid·웹 로그), 로컬 AI 로그는 `out/vlm.log`. 로컬 AI만 따로 켜려면 `bash scripts/start_vlm.sh`.
창 하나로 켜고 Ctrl+C로 함께 끄는 방식은 `uv run receipt-evidence app`(Windows `run-app.bat`과 같음)입니다.

---

## 사용법

### 로컬 AI 모델
영수증은 **Qwen3-VL-4B-Instruct(Q4_K_M, 이미지 인코더 mmproj Q8_0)** 로 읽습니다. `VLM_VARIANT=8b`로 Qwen3-VL-8B로 바꿀 수 있습니다(`run-app.bat`·`./run-app.sh`·`scripts/start_vlm.sh`·웹앱·CLI 공통).
```bash
uvx --from huggingface_hub hf download Qwen/Qwen3-VL-8B-Instruct-GGUF Qwen3VL-8B-Instruct-Q4_K_M.gguf mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf
VLM_VARIANT=8b ./run-app.sh restart     # 맥·리눅스. Windows는 set VLM_VARIANT=8b 후 run-app.bat
```
- 2026-09-14 시연 영수증 3건 비교: 4B는 판정·금액이 8B와 같고 읽는 시간은 약 1/2~1/3(8B 88초 → 4B 24~56초, Apple M5 Pro). 4B가 결제 시각을 빠뜨리면 전사문의 같은 날짜 시각으로 채웁니다.
- 읽은 결과 캐시는 모델별(`out/.cache/extract/p1/4b|8b`)이라, 모델을 바꾸면 새 영수증부터 그 모델로 읽습니다. 이미 읽어 둔 출장은 '다시 읽기'를 눌러야 바뀐 모델로 읽습니다.
- llama-server는 `LLAMA_SERVER` 환경변수 → 저장소 `tools/llama.cpp/` → `PATH` 순서로 찾고, 모델 파일은 허깅페이스 캐시(`HF_HUB_CACHE`·`HF_HOME`)에서 찾습니다. `VLM_MODEL`·`VLM_MMPROJ`로 파일을 직접 지정할 수도 있습니다.

### 웹앱 화면
홈(출장 목록) → 새 정산 → ① 영수증 올리기 → ② 읽은 값 확인 → ③ 판정 검토 → ④ 서류 완성(미리보기·내려받기·버전 이력).
- ① 첫 화면은 **출장자와 영수증 파일만** 받습니다. 파일을 놓으면 바로 읽기 시작합니다(새 출장자는 여비 구분 제2호 기본).
- ② 출장 기간·출장지·근무지·경로를 영수증(철도 운행일·가는 편 도착역, 숙박 체크인·아웃)으로 채운 **출장 정보 카드**를 보여 주고, "맞아요" 한 번으로 확정합니다. 결제일과 가맹점 주소는 근거로 쓰지 않습니다. 임시 폴더(`_새정산-시각`)는 읽은 뒤 `YYYY-MM-DD_출장지`로 이름이 바뀝니다.
- ③ 교통 영수증이 없으면 공용차량 이용 여부, 근무지와 출장지가 같으면 근무지 내 출장 여부를 그때만 묻습니다.
- ③ 판정 표의 영수증 행마다 **판정 바꾸기**(규정대로·인정·감액·불인정, 사유 필수)가 있습니다. 원래 규정 판정은 함께 남고, 서류의 근거 칸에 '담당자 판정', 별도 '담당자 판정 내역' 절에 사유가 적힙니다. 규정보다 많이 인정하면 '규정 한도 초과 인정'으로 표시됩니다(일비·식비는 대상 아님). 숙박 상한 초과 행에서는 부득이한 사유를 넣어 규정대로 10분의 3까지 추가지급할 수 있습니다.
- 일비·식비는 여행일수(시작일~종료일 포함, 1박 2일=2일) × 일액입니다. 출장 정보를 확정하기 전이라도 **가는 편·오는 편 교통 영수증이 모두 있거나 숙박 체크인·체크아웃이 있으면** 제안 기간으로 계산하고, 근거가 부족하면 확정 후 지급합니다. 일비·식비·근무지 내 출장 여비 행에도 **판정 바꾸기**(인정 일수·금액 지정·불인정, 사유 필수)가 있습니다 — 예: 교육기관 식사 제공 시 식비 금액 지정. `trip.yaml`의 `allowance_decisions`에 저장됩니다.
- 출장 삭제: 홈 카드의 휴지통 아이콘이나 올리기 화면의 '출장 삭제'로 지우면 `data/.trash`·`out/.trash`로 옮겨지고(목록·CLI에서 제외), 홈의 '되돌리기'나 '휴지통' 화면에서 복구·영구 삭제할 수 있습니다. 같은 이름의 출장이 이미 있으면 `_2`로 복구합니다.
- ④ 출장 목적(선택)·기관·부서·결재선은 서류 만들기 직전 "서류에 들어갈 정보"에서 받고, 기관·결재선은 출장자별로 기억합니다.

웹앱만 띄울 수도 있습니다. 이때 로컬 AI는 새 영수증을 읽을 때만 자동으로 켜지고 다 읽으면 꺼집니다(`run` 명령도 같음).
```bash
uv run receipt-evidence web                 # http://127.0.0.1:8780 (이 컴퓨터에서만 열림)
```

### 영수증 폴더로 일괄 처리(CLI)
웹앱 대신 폴더에 영수증을 넣고 한꺼번에 처리할 수도 있습니다.
```
data/<출장자>/traveler.yaml                 여비 구분·근무지·결재선 (examples/traveler.yaml)
data/<출장자>/<YYYY-MM-DD_출장지>/trip.yaml   출장기간·출장지·목적 (없으면 자동 제안)
data/<출장자>/<YYYY-MM-DD_출장지>/overrides.yaml  사용자 확인값 (선택)
data/<출장자>/<YYYY-MM-DD_출장지>/*.jpg|png|pdf|heic  영수증
```
위 구조(`data/<출장자>/<출장>/`)로 넣어야 처리됩니다.
```bash
uv run receipt-evidence run                 # 전체 일괄 (llama-server는 필요할 때 자동으로 켜지고 끝나면 꺼짐)
uv run receipt-evidence run --traveler 홍길동 --trip 2026-07-09_서울 --workers 4
```
- 다시 실행하면 이미 읽은 영수증은 캐시를 쓰고, 입력이 바뀐 출장만 새 버전(`v2`, `v3` …)을 만듭니다.
- 결과: `out/<출장자>/<출장>/v<N>/evidence.hwpx`, 요약 `out/summary-<run_id>.md`.
- `--workers 4`로 동시에 읽으려면 로컬 AI도 같은 수로 켭니다: `VLM_PARALLEL=4 uv run receipt-evidence vlm`(Windows는 `set VLM_PARALLEL=4` 후 `uv run receipt-evidence vlm`).

## 인터넷 없이 쓰기
인터넷이 될 때 한 번 준비해 두면(`setup-windows.bat` 또는 아래 명령) 이후에는 오프라인으로 끝까지 정산할 수 있습니다.
```bash
uv run receipt-evidence prepare      # 최신 여비 규정 조회 · MCP 패키지(korean-law-mcp·kordoc) 내려받기 · 로컬 AI 확인
```
| 구성 | 인터넷 | 없을 때 |
|---|---|---|
| 영수증 읽기(llama-server·Qwen3-VL) | 불필요 | — |
| HWPX 생성·검증(kordoc) | 준비 후 불필요 | `npx --prefer-offline`으로 받아 둔 패키지 사용(버전 고정) |
| 여비 규정(korean-law-mcp) | 조회에 필요 | 받아 둔 가장 최근 규정으로 판정하고 결과·서류에 안내를 남김. 없으면 저장소 기준본(2026. 7. 1. 시행, MST 287535) |
| 웹 글꼴 | 불필요 | 저장소에 동봉 |

- 규정은 하루 한 번 현행본을 조회합니다. 조회에 실패하면 10분 동안은 다시 조회하지 않고 저장해 둔 규정을 씁니다(`--refresh-law`로 즉시 재조회).
- 받은 규정은 버전(MST)별로 `out/.cache/law/snapshots/`에 쌓이고, 출장 시작일에 시행되던 규정으로 판정합니다. 그 규정이 없으면 현행 규정으로 판정하고 안내만 붙입니다.
- 개정이 감지되면(`out/.cache/law/amendments.json`) 홈·판정 화면과 요약에 알립니다. 근무지 내 출장 금액·추가지급 한도 등은 조문 본문에서 읽고, 문구가 바뀌어 못 읽으면 해당 항목을 확인필요로 둡니다.
- 환경변수: `KOREAN_LAW_MCP`(기본 `korean-law-mcp@4.13.0`), `KORDOC_MCP`(기본 `kordoc@4.13.1`), `LAW_OC`(국가법령정보 공동활용 OC).

## 폴더 구조
```
setup-windows.bat · run-app.bat      Windows 설정·실행 (scripts/windows/*.ps1 호출)
run-app.sh · scripts/start_vlm.sh    macOS·Linux 실행
src/receipt_evidence/                앱 코드 (cli.py 명령, web/ 웹앱, vlm_server.py 로컬 AI 실행)
tests/                               단위·통합 테스트
examples/                            traveler.yaml·trip.yaml·overrides.yaml 예시
docs/                                바이브코딩 따라하기, 설계·계획 문서, 화면 시안(docs/design)
data/ · out/                         내 영수증·결과물 (git에 올라가지 않음)
tools/llama.cpp/                     Windows 설정이 내려받는 llama.cpp (git에 올라가지 않음)
```

## 테스트
```bash
uv run pytest                    # 단위 테스트 (네트워크·VLM 불필요)
uv run pytest -m integration     # 실제 llama-server·MCP 연동
```

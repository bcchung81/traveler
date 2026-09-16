# 바이브코딩 따라하기 — 환경 설정 · MCP · 스킬 (15분)

> 코드는 읽지 않고 **한국어 문장으로 지시**하면, AI(Claude Code)가 내 PC의 프로젝트 폴더를 읽고 → 계획하고 → 만들고 → 테스트까지 돌립니다.
> 전파연 AI 학습동호회 발표(2026. 9.) 9·10·11·12·14쪽 요약 · Windows 기준(맥은 괄호).

**준비:** 개인 PC(업무망 PC는 정보화 부서 설치 승인 필요 · 업무 자료는 올리지 않기) · Claude **Pro 이상** 계정(무료 요금제는 Claude Code 불가)

### 1단계. 기본 도구 설치 — PowerShell(맥: 터미널)에서 한 줄씩
```powershell
winget install OpenJS.NodeJS.LTS   # Node.js — npx로 MCP 실행   (맥: brew install node)
winget install astral-sh.uv        # uv — uvx로 MCP 실행          (맥: brew install uv)
winget install --id Git.Git -e --source winget   # Git — 저장소 받기·이력·되돌리기 (맥: xcode-select --install)
```
→ **PowerShell을 닫고 새로 엽니다**(설치한 명령을 인식하게).

### 2단계. Claude Code 설치 · 로그인
```powershell
irm https://claude.ai/install.ps1 | iex   # 공식 설치기(자동 업데이트) · 또는 winget install Anthropic.ClaudeCode
                                          # 맥: curl -fsSL https://claude.ai/install.sh | bash
cd C:\작업폴더                             # 일할 프로젝트 폴더로 이동
# 예) 이 저장소로 시작: git clone https://github.com/bcchung81/traveler.git ; cd traveler
claude                                    # 처음 실행하면 브라우저 로그인 안내대로
```
✅ 확인: `node -v` · `uv --version` · `git --version` · `claude --version` 넷 다 버전이 나오면 끝

### 3단계. MCP 붙이기 — AI에 '손'을 달기 (엑셀·한글·법령)
```powershell
claude mcp add -s user excel -- uvx excel-mcp-server stdio
claude mcp add -s user kordoc -- npx -y kordoc mcp
claude mcp add -s user korean-law -e LAW_OC=발급키 -- npx -y korean-law-mcp
claude mcp list                           # 셋 다 ✔ Connected 면 성공
```
- `-s user`는 **모든 폴더**에서 쓰기(빼면 지금 폴더에서만). 법령 키 `LAW_OC`는 [open.law.go.kr](https://open.law.go.kr)에서 **각자 무료 발급**.
- `-e`는 반드시 **이름 뒤**에 둡니다. Claude Code 안에서는 `/mcp`로 상태를 봅니다.

### 4단계. 스킬 넣기 — AI에 '절차서'를 주기
스킬 = `SKILL.md`가 든 폴더 하나. **같은 지시를 두 번째 붙여 넣는 순간 스킬로 만듭니다.**

| 방법 | 하는 법 |
|---|---|
| 직접 만들기 | `.claude\skills\<이름>\SKILL.md`(이 폴더만) 또는 `%USERPROFILE%\.claude\skills\<이름>\SKILL.md`(내 PC 전체) |
| 남이 만든 스킬 | `npx skills add anthropics/skills -g` — [skills.sh](https://skills.sh)에서 찾아 설치 |
| 플러그인 묶음 | Claude Code 안에서 `/plugin install superpowers@claude-plugins-official` (계획→검증 절차) |

```markdown
---
name: report-merge
description: 부서별 실적 엑셀을 합칠 때 쓴다. 처음 보는 열이면 사람에게 묻는다.
---
# 절차  1) excel MCP로 연다  2) 계산은 스크립트로  3) 모르는 값은 검토사항으로 적는다
```
부르는 법: AI가 `description`을 보고 알아서, 또는 `/report-merge`(이름은 영문 소문자·숫자·하이픈). 이 저장소 폴더에서 `claude`를 켜면 `receipt-evidence` 스킬이 자동으로 잡힙니다.

### 5단계. 첫 지시
```text
> 영수증 사진을 읽어 합계를 엑셀로 정리하는 스크립트를 만들고, 테스트까지 돌려 줘
```

### 막히면 — PowerShell 오류 세 가지
| 보이는 메시지 | 해결 |
|---|---|
| `claude : 용어가 … 인식되지 않습니다` | PowerShell을 새로 연다 → 안 되면 설치 폴더 `%USERPROFILE%\.local\bin`(npm으로 깔았다면 `npm config get prefix` 경로)을 Path에 추가 |
| `이 시스템에서 스크립트를 실행할 수 없으므로…` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` → `Y` → 새로 열기(내 계정만 바뀜) |
| `npm ERR! code EACCES` / `EPERM` | PowerShell을 **관리자 권한**으로 열거나, 2단계의 공식 설치기(관리자 권한 불필요) 사용 |

더 보기: [Claude Code 설치](https://code.claude.com/docs/en/setup) · [MCP](https://code.claude.com/docs/en/mcp) · [스킬](https://code.claude.com/docs/en/skills) · [modelcontextprotocol.io](https://modelcontextprotocol.io) · [agentskills.io](https://agentskills.io)

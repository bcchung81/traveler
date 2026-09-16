<#
.SYNOPSIS
  여비정산 증빙 웹앱 실행(Windows). 로컬 AI(llama-server :8088)를 켜고 준비되면 웹앱(:8780)을 켠 뒤 브라우저를 연다.
  이 창을 닫거나 Ctrl+C를 누르면 웹앱과 로컬 AI가 함께 꺼진다. 보통은 저장소 폴더의 run-app.bat을 더블클릭한다.

.EXAMPLE
  run-app.bat                      기본 포트로 실행
  run-app.bat --port 8790          웹앱 포트 바꾸기 (--vlm-port 8089 로 로컬 AI 포트도)
  run-app.bat --no-browser         브라우저를 열지 않기
#>
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-Location $Root
Update-SessionPath

if (-not (Test-Command uv)) { Stop-WithError 'uv가 없어요 — 먼저 setup-windows.bat을 실행하세요' }
if (-not (Test-Path (Join-Path $Root '.venv'))) { Stop-WithError '아직 설정 전이에요 — 먼저 setup-windows.bat을 실행하세요' }
if (-not (Test-Command npx)) { Write-Warn 'Node.js(npx)가 없어 서류 만들기·규정 조회가 안 돼요 — setup-windows.bat을 다시 실행하세요' }

# 인터넷이 없어도 설치된 패키지로 켠다(git pull로 패키지가 바뀌었을 때만 내려받기)
$code = (Invoke-NativeCapture uv @('sync', '--frozen', '--offline', '-q')).Code
if ($code -ne 0) { $code = Invoke-Native uv @('sync', '--frozen', '-q') }
if ($code -ne 0) { Stop-WithError '파이썬 패키지를 맞추지 못했어요. 인터넷 연결을 확인하세요' }

Write-Title '여비정산 증빙 웹앱'
$ErrorActionPreference = 'Continue'
& uv run --frozen --offline receipt-evidence app @args
exit $LASTEXITCODE

# 여비정산 증빙 Windows 스크립트 공통 함수 (Windows PowerShell 5.1 호환 — ?? ?. && 같은 PowerShell 7 문법은 쓰지 않는다)
# 이 파일은 UTF-8(BOM)·CRLF로 저장한다. BOM이 없으면 Windows PowerShell 5.1이 한글을 깨뜨린다.

$script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

function Write-Title([string]$Text) {
    Write-Host ''
    Write-Host "== $Text ==" -ForegroundColor Cyan
}

function Write-Step([string]$No, [string]$Text) {
    Write-Host ''
    Write-Host "[$No] $Text" -ForegroundColor Yellow
}

function Write-Ok([string]$Text) { Write-Host "  OK  $Text" -ForegroundColor Green }
function Write-Info([string]$Text) { Write-Host "      $Text" }
function Write-Warn([string]$Text) { Write-Host "  !!  $Text" -ForegroundColor Magenta }

function Stop-WithError([string]$Text) {
    Write-Host ''
    Write-Host "  실패: $Text" -ForegroundColor Red
    exit 1
}

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# 설치 프로그램이 바꾼 PATH를 새 창을 열지 않고 이 창에 반영한다
function Update-SessionPath {
    if ($env:OS -ne 'Windows_NT') { return }
    $parts = @(
        (Join-Path $HOME '.local\bin'),
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        $env:Path
    )
    $env:Path = ($parts | Where-Object { $_ }) -join ';'
}

# 외부 프로그램 실행: stderr에 진행 표시를 쓰는 프로그램(uv·hf·winget)이 PowerShell 5.1에서 오류로 바뀌지 않게 한다
# 실행조차 못 하면(파일 없음·DLL 없음 등) 종료 코드 -1과 사유를 돌려준다
function Invoke-Native([string]$File, [string[]]$ArgList) {
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    try {
        & $File @ArgList | Out-Host
    } catch {
        Write-Warn "$File 실행 실패: $($_.Exception.Message)"
        return -1
    }
    return $LASTEXITCODE
}

# 출력까지 받아 온다(stdout·stderr를 문자열로)
function Invoke-NativeCapture([string]$File, [string[]]$ArgList) {
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    try {
        $lines = & $File @ArgList 2>&1 | ForEach-Object { "$_" }
        return [pscustomobject]@{ Code = $LASTEXITCODE; Lines = @($lines) }
    } catch {
        return [pscustomobject]@{ Code = -1; Lines = @("$File 실행 실패: $($_.Exception.Message)") }
    }
}

function Get-KeyValue([string[]]$Lines, [string]$Key) {
    foreach ($line in $Lines) {
        if ($line.StartsWith("$Key=")) { return $line.Substring($Key.Length + 1).Trim() }
    }
    return ''
}

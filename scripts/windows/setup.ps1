<#
.SYNOPSIS
  여비정산 증빙(receipt-evidence) Windows 환경 설정.
  uv(파이썬 3.14 포함) · Node.js · llama.cpp(llama-server) · Qwen3-VL 모델 · 여비 규정/HWPX 도구를 차례로 준비한다.
  이미 있는 것은 건너뛰므로 여러 번 실행해도 된다. 보통은 저장소 폴더의 setup-windows.bat을 더블클릭한다.

.PARAMETER Backend
  llama.cpp 빌드. auto(기본: NVIDIA 그래픽카드면 cuda, 아니면 vulkan) | cuda | vulkan | cpu

.PARAMETER LlamaBuild
  llama.cpp 릴리스 태그. 기본 b9740(이 저장소에서 Qwen3-VL 4B로 검증한 빌드)

.PARAMETER ReinstallLlama
  tools\llama.cpp를 지우고 다시 내려받는다(예: -Backend를 바꿀 때)

.PARAMETER SkipModel
  모델(약 3GB) 내려받기를 건너뛴다

.PARAMETER LawOc
  법제처 Open API 인증키(OC). https://open.law.go.kr 에서 각자 무료 발급. 주면 사용자 환경변수 LAW_OC로 저장한다.
  없으면 최신 규정을 조회하지 않고 저장소에 들어 있는 기준 규정으로 판정한다.

.PARAMETER Interactive
  인증키가 없을 때 입력을 묻는다(setup-windows.bat이 붙인다. Claude Code 등에서 직접 실행할 때는 빼서 멈추지 않게 한다)

.PARAMETER DryRun
  설치·내려받기 없이 무엇을 할지만 보여 준다

.EXAMPLE
  setup-windows.bat -Backend vulkan -ReinstallLlama
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1 -LawOc 발급키
#>
param(
    [ValidateSet('auto', 'cuda', 'vulkan', 'cpu')] [string]$Backend = 'auto',
    [string]$LlamaBuild = 'b9740',
    [switch]$ReinstallLlama,
    [switch]$SkipModel,
    [string]$LawOc,
    [switch]$Interactive,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Windows PowerShell 5.1은 진행 막대를 그리느라 내려받기가 매우 느려진다
. (Join-Path $PSScriptRoot 'common.ps1')
Set-Location $Root
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

$NodeMin = [version]'20.19.0'  # korean-law-mcp 요구 버전
$LlamaDir = Join-Path $Root 'tools\llama.cpp'
$LlamaExe = Join-Path $LlamaDir 'llama-server.exe'

function Invoke-Change([string]$What, [scriptblock]$Action) {
    if ($DryRun) {
        Write-Info "(DryRun) $What"
        return
    }
    Write-Info $What
    & $Action
}

function Install-Winget([string]$Id, [string]$Label, [string]$ManualUrl) {
    if (-not (Test-Command winget)) {
        Stop-WithError "$Label 설치에 winget(앱 설치 관리자)이 필요해요. $ManualUrl 에서 직접 설치한 뒤 다시 실행하세요"
    }
    Invoke-Change "winget으로 $Label 설치 ($Id) — 관리자 권한 창이 뜨면 '예'를 누르세요" {
        $code = Invoke-Native winget @('install', '--id', $Id, '-e', '--accept-source-agreements', '--accept-package-agreements')
        if ($code -ne 0) { Write-Warn "winget 종료 코드 $code — 설치됐는지 아래에서 다시 확인합니다" }
        Update-SessionPath
    }
}

Write-Title '여비정산 증빙 — Windows 환경 설정'
Write-Info "저장소 폴더: $Root"
if ($DryRun) { Write-Warn 'DryRun: 설치·내려받기를 하지 않고 할 일만 보여 줍니다' }
if ($env:OS -eq 'Windows_NT' -and -not [Environment]::Is64BitOperatingSystem) { Stop-WithError '64비트 Windows 10/11이 필요해요' }
Update-SessionPath
Import-UserVariable 'LAW_OC'

# ---------------------------------------------------------------- 1. uv
Write-Step '1/6' 'uv (파이썬과 패키지 관리)'
if (Test-Command uv) {
    Write-Ok "설치돼 있음 — $((Invoke-NativeCapture uv @('--version')).Lines -join ' ')"
} else {
    Invoke-Change 'uv 설치 (https://astral.sh/uv)' {
        $code = Invoke-Native powershell @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', 'irm https://astral.sh/uv/install.ps1 | iex')
        if ($code -ne 0) { Stop-WithError "uv 설치 실패(종료 코드 $code). 인터넷 연결을 확인하세요" }
        Update-SessionPath
        if (-not (Test-Command uv)) { Stop-WithError 'uv를 설치했지만 찾지 못했어요. 이 창을 닫고 setup-windows.bat을 다시 실행하세요' }
        Write-Ok 'uv 설치 완료'
    }
}

# ---------------------------------------------------------------- 2. Node.js
Write-Step '2/6' "Node.js $NodeMin 이상 (여비 규정 조회·HWPX 도구 실행)"
$nodeVersion = $null
if (Test-Command node) {
    $raw = ((Invoke-NativeCapture node @('--version')).Lines -join '').Trim().TrimStart('v')
    try { $nodeVersion = [version]$raw } catch { $nodeVersion = $null }
}
if ($nodeVersion -and $nodeVersion -ge $NodeMin -and (Test-Command npx)) {
    Write-Ok "설치돼 있음 — v$nodeVersion"
} else {
    if ($nodeVersion) { Write-Warn "Node.js v$nodeVersion 은 너무 오래됐어요 — LTS로 올립니다" }
    Install-Winget 'OpenJS.NodeJS.LTS' 'Node.js LTS' 'https://nodejs.org/ko'
    if (-not $DryRun) {
        if (-not (Test-Command npx)) { Stop-WithError 'Node.js를 설치했지만 npx를 찾지 못했어요. 이 창을 닫고 setup-windows.bat을 다시 실행하세요' }
        Write-Ok "Node.js 준비됨 — $((Invoke-NativeCapture node @('--version')).Lines -join '')"
    }
}

# ---------------------------------------------------------------- 3. Python 패키지
Write-Step '3/6' '파이썬 3.14와 패키지 (uv sync)'
Invoke-Change 'uv sync --frozen (처음에는 파이썬 내려받기 포함 1~3분)' {
    $code = Invoke-Native uv @('sync', '--frozen')
    if ($code -ne 0) { Stop-WithError "uv sync 실패(종료 코드 $code). 인터넷 연결을 확인하고 다시 실행하세요" }
    Write-Ok '파이썬 환경 준비됨 (.venv)'
}

# ---------------------------------------------------------------- 4. llama.cpp
Write-Step '4/6' "로컬 AI 실행기 llama.cpp ($LlamaBuild)"
$useExisting = $null
if ($env:LLAMA_SERVER) {
    $useExisting = $env:LLAMA_SERVER
} elseif ((Test-Path $LlamaExe) -and -not $ReinstallLlama) {
    $useExisting = $LlamaExe
} elseif (-not (Test-Path $LlamaExe) -and (Test-Command llama-server)) {
    $useExisting = (Get-Command llama-server).Source
}

if ($useExisting -and -not $ReinstallLlama) {
    Write-Ok "사용할 llama-server: $useExisting"
    $llama = $useExisting
} else {
    $gpu = @()
    try { $gpu = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) } catch { }
    if ($gpu.Count -gt 0) { Write-Info "그래픽카드: $($gpu -join ', ')" }
    if ($Backend -eq 'auto') {
        if (Test-Command nvidia-smi) { $Backend = 'cuda' } else { $Backend = 'vulkan' }
    }
    switch ($Backend) {
        'cuda'   { $assets = @("llama-$LlamaBuild-bin-win-cuda-12.4-x64.zip", 'cudart-llama-bin-win-cuda-12.4-x64.zip'); $size = '약 650MB' }
        'vulkan' { $assets = @("llama-$LlamaBuild-bin-win-vulkan-x64.zip"); $size = '약 40MB' }
        'cpu'    { $assets = @("llama-$LlamaBuild-bin-win-cpu-x64.zip"); $size = '약 17MB' }
    }
    Invoke-Change "llama.cpp $Backend 빌드 내려받기($size) → tools\llama.cpp" {
        if (Test-Path $LlamaDir) { Remove-Item -Recurse -Force $LlamaDir }
        New-Item -ItemType Directory -Force -Path $LlamaDir | Out-Null
        $tmp = Join-Path ([IO.Path]::GetTempPath()) "receipt-evidence-llama-$LlamaBuild"
        New-Item -ItemType Directory -Force -Path $tmp | Out-Null
        foreach ($asset in $assets) {
            $url = "https://github.com/ggml-org/llama.cpp/releases/download/$LlamaBuild/$asset"
            $zip = Join-Path $tmp $asset
            Write-Info "내려받는 중: $url"
            try {
                Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
            } catch {
                Stop-WithError "내려받기 실패: $url — $($_.Exception.Message)"
            }
            Expand-Archive -Path $zip -DestinationPath $LlamaDir -Force
            Remove-Item -Force $zip
        }
        if (-not (Test-Path $LlamaExe)) { Stop-WithError "압축을 풀었지만 llama-server.exe가 없어요: $LlamaDir" }
        Write-Ok "llama.cpp 설치됨 — $LlamaExe"
    }
    $llama = $LlamaExe
}

if (-not $DryRun) {
    $run = Invoke-NativeCapture $llama @('--version')
    if ($run.Code -ne 0) {
        Write-Warn "llama-server가 실행되지 않았어요(종료 코드 $($run.Code)). Visual C++ 런타임을 설치하고 다시 확인합니다"
        Install-Winget 'Microsoft.VCRedist.2015+.x64' 'Visual C++ 재배포 패키지' 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
        $run = Invoke-NativeCapture $llama @('--version')
    }
    if ($run.Code -ne 0) {
        $run.Lines | Select-Object -Last 5 | ForEach-Object { Write-Info $_ }
        Stop-WithError 'llama-server를 실행하지 못했어요. setup-windows.bat -Backend cpu -ReinstallLlama 로 CPU 빌드를 시험해 보세요'
    }
    Write-Ok (($run.Lines | Where-Object { $_ -match '^version' } | Select-Object -First 1))
    $devices = Invoke-NativeCapture $llama @('--list-devices')
    $gpuLines = @($devices.Lines | Where-Object { $_ -match '^\s+(CUDA|Vulkan|SYCL|OpenCL)\d*:' })
    if ($gpuLines.Count -gt 0) {
        $gpuLines | ForEach-Object { Write-Ok "GPU 사용 가능: $($_.Trim())" }
    } else {
        Write-Warn 'llama.cpp가 GPU를 찾지 못해 CPU로 읽어요(영수증 1장에 수 분 걸릴 수 있음). 그래픽 드라이버를 최신으로 올리거나 -Backend vulkan -ReinstallLlama 를 시험해 보세요'
    }
}

# ---------------------------------------------------------------- 5. 모델
Write-Step '5/6' '영수증을 읽는 모델 Qwen3-VL (허깅페이스, 약 3GB)'
if ($DryRun -and -not (Test-Path (Join-Path $Root '.venv'))) {
    Write-Info '(DryRun) 파이썬 환경이 아직 없어 모델 확인을 건너뜁니다'
} else {
    $check = Invoke-NativeCapture uv @('run', '--frozen', 'receipt-evidence', 'vlm', '--dry-run')
    $repo = Get-KeyValue $check.Lines 'repo'
    $files = Get-KeyValue $check.Lines 'files'
    $model = Get-KeyValue $check.Lines 'model'
    if (-not $repo) {
        $check.Lines | Select-Object -Last 5 | ForEach-Object { Write-Info $_ }
        Stop-WithError '모델 정보를 읽지 못했어요(uv run receipt-evidence vlm --dry-run)'
    }
    if ($model) {
        Write-Ok "모델 있음 — $model"
    } elseif ($SkipModel) {
        Write-Warn "모델 내려받기를 건너뜀(-SkipModel). 나중에: uvx --from huggingface_hub hf download $repo $files"
    } else {
        Invoke-Change "hf download $repo $files" {
            $hfArgs = @('--from', 'huggingface_hub', 'hf', 'download', $repo) + @($files -split ' ')
            $code = Invoke-Native uvx $hfArgs
            if ($code -ne 0) { Stop-WithError "모델 내려받기 실패(종료 코드 $code). 인터넷 연결을 확인하고 다시 실행하세요(받은 부분은 이어 받습니다)" }
            $again = Invoke-NativeCapture uv @('run', '--frozen', 'receipt-evidence', 'vlm', '--dry-run')
            if ($again.Code -ne 0) {
                $again.Lines | Select-Object -Last 3 | ForEach-Object { Write-Info $_ }
                Stop-WithError '모델을 내려받았지만 찾지 못했어요'
            }
            Write-Ok "모델 준비됨 — $(Get-KeyValue $again.Lines 'model')"
        }
    }
}

# ---------------------------------------------------------------- 6. 인증키·규정·HWPX 도구
Write-Step '6/6' '법제처 인증키 · 여비 규정 조회 · HWPX 도구(kordoc) 내려받기 · 전체 점검 (receipt-evidence prepare)'
$oc = if ($LawOc) { $LawOc.Trim() } else { "$env:LAW_OC".Trim() }
if (-not $oc -and $Interactive -and -not $DryRun) {
    Write-Info '최신 여비 규정을 조회하려면 법제처 Open API 인증키(OC)가 필요해요(무료, 발급받은 본인만 사용).'
    Write-Info '발급: https://open.law.go.kr 회원가입·로그인 → OPEN API → Open API 사용 신청'
    $oc = "$(Read-Host '      인증키(OC)를 입력하세요. 아직 없으면 그냥 Enter')".Trim()
}
if ($oc) {
    $saved = if ($env:OS -eq 'Windows_NT') { [Environment]::GetEnvironmentVariable('LAW_OC', 'User') } else { $null }
    if ($saved -ne $oc) {
        Invoke-Change '인증키를 사용자 환경변수 LAW_OC로 저장(다음부터 run-app.bat이 자동으로 씀)' {
            if ($env:OS -eq 'Windows_NT') { [Environment]::SetEnvironmentVariable('LAW_OC', $oc, 'User') }
        }
    }
    $env:LAW_OC = $oc
    Write-Ok '법제처 인증키(LAW_OC) 설정됨'
} else {
    Write-Warn '인증키 없이 진행 — 저장소에 들어 있는 기준 규정(2026. 7. 1. 시행)으로 판정해요'
    Write-Info '나중에 넣으려면: setup-windows.bat -LawOc 발급키'
}
Invoke-Change 'uv run receipt-evidence prepare (처음에는 npm 패키지 내려받기로 1~2분)' {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root 'data') | Out-Null
    $code = Invoke-Native uv @('run', '--frozen', 'receipt-evidence', 'prepare')
    if ($code -ne 0) {
        Write-Warn '일부 준비가 끝나지 않았어요(위 메시지 참고). 인터넷 연결 후 setup-windows.bat을 다시 실행해도 됩니다'
        Write-Warn '여비 규정 조회에 실패해도 저장소에 들어 있는 기준 규정으로 판정할 수 있어요'
    }
}

Write-Title '설정 완료'
Write-Host '  이제 run-app.bat을 더블클릭하면 로컬 AI와 웹앱이 켜지고 브라우저가 열립니다.' -ForegroundColor Green
Write-Host '  주소: http://127.0.0.1:8780   끄기: 그 창을 닫거나 Ctrl+C' -ForegroundColor Green
exit 0

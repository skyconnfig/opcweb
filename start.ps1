$ErrorActionPreference = 'Stop'
$reload = $args -contains '--reload'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'

function Get-ProjectPortProcesses([int] $port) {
  $listenerIds = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
  foreach ($processId in $listenerIds) {
    Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
  }
}

function Test-ProjectPort([int] $port) {
  $processes = @(Get-ProjectPortProcesses $port)
  if (-not $processes) { return $false }
  $rootPattern = [regex]::Escape($root)
  $pythonPattern = [regex]::Escape($python)
  foreach ($process in $processes) {
    $commandLine = [string]$process.CommandLine
    if (($commandLine -match $rootPattern) -or (($commandLine -match $pythonPattern) -and ($commandLine -match 'uvicorn\s+app\.main:app') -and ($commandLine -match "--port\s+$port"))) {
      return $true
    }
  }
  throw "端口 $port 已被其他进程占用，请先处理端口占用后再启动。"
}

function Test-WebStaticAssets {
  try {
    $html = (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:5173/' -TimeoutSec 5).Content
    $assetUrls = [regex]::Matches($html, '(?:src|href)="([^"]*?/_next/static/[^"]+)"') |
      ForEach-Object { $_.Groups[1].Value } |
      Where-Object { $_ } |
      Select-Object -Unique
    if (-not $assetUrls) { return $false }
    foreach ($assetUrl in $assetUrls) {
      $url = if ($assetUrl.StartsWith('/')) { 'http://127.0.0.1:5173' + $assetUrl } else { $assetUrl }
      $asset = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 5
      $contentType = [string]$asset.Headers['Content-Type']
      if ($asset.StatusCode -ne 200 -or $asset.Content.Length -lt 100 -or $contentType -match '(?i)text/html') { return $false }
    }
    return $true
  } catch {
    return $false
  }
}

function Stop-ProjectWebProcesses {
  $rootPattern = [regex]::Escape($root)
  $projectWebProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $commandLine = [string]$_.CommandLine
    ($commandLine -match $rootPattern) -and
      ($commandLine -match '(?i)(next[\\/]dist[\\/]server[\\/]lib[\\/]start-server\.js|next[\\/]dist[\\/]bin[\\/]next|npm(?:\.cmd)?\s+run\s+dev)')
  })
  foreach ($process in $projectWebProcesses | Sort-Object ProcessId -Descending) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
  }
  for ($attempt = 0; $attempt -lt 20; $attempt++) {
    $listeners = @(Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue)
    if (-not $listeners) { return }
    Start-Sleep -Milliseconds 250
  }
  throw 'Web 进程未能在预期时间内退出，请检查 5173 端口后重试。'
}

# A production build can invalidate the .next assets used by an existing dev
# server. Check every local CSS/JS asset before treating the Web process as ready.
$apiAlive = Test-ProjectPort 8689
$webAlive = Test-ProjectPort 5173
$webAssetsHealthy = $webAlive -and (Test-WebStaticAssets)
if ($apiAlive -and $webAssetsHealthy) {
  Write-Host 'AI Lead Radar 已在运行：API 8689，Web 5173。'
  exit 0
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'uv is required. Install uv first.' }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
  uv venv .venv --python 3.13
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js 20+ is required.' }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'npm is required.' }
uv pip install -e ".[test]" --python .venv\Scripts\python.exe
if (-not (Test-Path -LiteralPath '.env')) {
  Copy-Item -LiteralPath '.env.example' -Destination '.env'
  $encryptionKey = & $python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
  Add-Content -LiteralPath '.env' -Value "SETTINGS_ENCRYPTION_KEY=$encryptionKey"
  Write-Host 'Created local .env and generated the API-key encryption key. Enter a new DeepSeek API key in Settings.' -ForegroundColor Yellow
}
if (-not (Test-Path -LiteralPath 'data')) { New-Item -ItemType Directory -Path 'data' | Out-Null }
$alembic = Join-Path $root '.venv\Scripts\alembic.exe'
if (-not (Test-Path -LiteralPath $alembic)) { throw 'Alembic was not installed.' }
& $alembic -c (Join-Path $root 'alembic.ini') upgrade head
$browserRoot = Join-Path $env:USERPROFILE 'AppData\Local\ms-playwright'
if (-not (Get-ChildItem -LiteralPath $browserRoot -Directory -Filter 'chromium-*' -ErrorAction SilentlyContinue)) { throw 'Playwright Chromium is missing. Run .venv\Scripts\playwright.exe install chromium.' }
if (-not (Test-Path -LiteralPath 'web\node_modules')) { Set-Location web; npm install; Set-Location $root }
$webNeedsRestart = $webAlive -and -not $webAssetsHealthy
if ($webNeedsRestart) {
  Stop-ProjectWebProcesses
}
$reloadArg = if ($reload) { ' --reload' } else { '' }
if (-not (Test-ProjectPort 8689)) {
  $serverCommand = "Set-Location '$root'; & '$python' -m uvicorn app.main:app --app-dir backend$reloadArg --loop app.uvicorn_loop:create_loop --port 8689"
  Start-Process -FilePath 'powershell' -ArgumentList @('-NoExit', '-Command', $serverCommand) -WindowStyle Hidden
}
Set-Location web
if (-not (Test-ProjectPort 5173)) {
  npm run dev -- --port 5173
}

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

# A second invocation should be harmless when both project services are alive.
if ((Test-ProjectPort 8689) -and (Test-ProjectPort 5173)) {
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
  $encryptionKey = & '.venv\Scripts\python.exe' -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
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
$reloadArg = if ($reload) { ' --reload' } else { '' }
if (-not (Test-ProjectPort 8689)) {
  Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$root'; .\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend$reloadArg --loop app.uvicorn_loop:create_loop --port 8689" -WindowStyle Hidden
}
Set-Location web
if (-not (Test-ProjectPort 5173)) {
  npm run dev -- --port 5173
}

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
  uv venv .venv --python 3.13
}
uv pip install -e ".[test]" --python .venv\Scripts\python.exe
if (-not (Test-Path -LiteralPath 'web\node_modules')) { Set-Location web; npm install; Set-Location $root }
Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$root'; .\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --port 8688" -WindowStyle Normal
Set-Location web
npm run dev


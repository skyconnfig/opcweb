$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$crawlerRoot = Join-Path $root '.references\douyin-comments-crawler'
$crawlerPython = Join-Path $crawlerRoot '.venv\Scripts\python.exe'
# The main API and the optional crawler must reuse the same persistent login
# profile.  They cannot safely open it at the same time, so the guard below
# prevents concurrent Chromium owners instead of silently creating a second
# profile that asks the user to log in again.
$profile = if ($env:DOUYIN_CRAWLER_PROFILE_DIR) {
  $env:DOUYIN_CRAWLER_PROFILE_DIR
} else {
  Join-Path $root 'data\browser\douyin'
}

if (-not (Test-Path -LiteralPath $crawlerPython)) {
  throw "未找到 crawler 虚拟环境：$crawlerPython"
}

$profile = [System.IO.Path]::GetFullPath($profile)
$normalizedProfile = $profile.TrimEnd('\', '/')
$profileOwners = Get-CimInstance Win32_Process | Where-Object {
  if ($_.Name -notmatch '^(chrome|msedge)(\.exe)?$' -or [string]::IsNullOrWhiteSpace($_.CommandLine)) {
    return $false
  }
  $commandLine = $_.CommandLine.Replace('"', '')
  return $commandLine.IndexOf("--user-data-dir=$normalizedProfile", [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}
if ($profileOwners) {
  $ownerIds = ($profileOwners | Select-Object -ExpandProperty ProcessId) -join ', '
  throw "Profile 正在被 Chromium 进程占用（PID: $ownerIds）。请先在抖音账号页面关闭浏览器，再启动 crawler；登录 Cookie 会继续复用，不需要重新登录。"
}

New-Item -ItemType Directory -Force -Path $profile | Out-Null
$env:DOUYIN_PROFILE_DIR = $profile
Set-Location $crawlerRoot
Write-Host "crawler 使用持久化 Profile：$env:DOUYIN_PROFILE_DIR" -ForegroundColor Cyan
Write-Host '该 Profile 与主 API 共用登录态；运行 crawler 期间不要打开主 API 的抖音浏览器。' -ForegroundColor Yellow
& $crawlerPython douyin_analysis_api_server.py

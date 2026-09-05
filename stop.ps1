$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Get-ProcessTree([int[]] $rootIds) {
  $all = @(Get-CimInstance Win32_Process)
  $ids = [System.Collections.Generic.HashSet[int]]::new()
  $pending = [System.Collections.Generic.Queue[int]]::new()
  foreach ($rootId in $rootIds) {
    if ($ids.Add($rootId)) { $pending.Enqueue($rootId) }
  }
  while ($pending.Count -gt 0) {
    $parentId = $pending.Dequeue()
    foreach ($process in $all | Where-Object { $_.ParentProcessId -eq $parentId }) {
      if ($ids.Add([int]$process.ProcessId)) { $pending.Enqueue([int]$process.ProcessId) }
    }
  }
  return @($ids)
}

$rootPattern = [regex]::Escape($root)
$pythonPattern = [regex]::Escape((Join-Path $root '.venv\Scripts\python.exe'))
$allProcesses = @(Get-CimInstance Win32_Process)
$targetIds = @()
foreach ($port in @(8689, 5173)) {
  $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
  foreach ($listener in $listeners) {
    $process = $allProcesses | Where-Object { $_.ProcessId -eq $listener.OwningProcess }
    if ($process -and ([string]$process.CommandLine -match $rootPattern -or ([string]$process.CommandLine -match 'uvicorn\s+app\.main:app' -and [string]$process.CommandLine -match "--port\s+$port"))) {
      $targetIds += [int]$process.ProcessId
    }
  }
}

# Include an orphaned API parent as well as the listener child. Uvicorn can
# leave this parent alive after a manually terminated child; it still owns the
# in-process task worker and must not compete for the same SQLite/Profile.
foreach ($process in $allProcesses) {
  $commandLine = [string]$process.CommandLine
  if (($commandLine -match $pythonPattern -and $commandLine -match 'uvicorn\s+app\.main:app' -and $commandLine -match '--port\s+8689') -or ($commandLine -match $rootPattern -and $commandLine -match '(?i)(next|npm\s+run\s+dev|start-server)' -and $commandLine -match '--port\s+5173')) {
    $targetIds += [int]$process.ProcessId
  }
}

$targetIds = @($targetIds | Select-Object -Unique)
if (-not $targetIds) {
  Write-Host 'No AI Lead Radar API/Web listener found.'
  exit 0
}

$treeIds = Get-ProcessTree $targetIds
$allById = @{}
foreach ($process in $allProcesses) { $allById[[int]$process.ProcessId] = $process }
$ancestorIds = [System.Collections.Generic.HashSet[int]]::new()
foreach ($targetId in $targetIds) {
  $current = $targetId
  while ($allById.ContainsKey($current)) {
    $parentId = [int]$allById[$current].ParentProcessId
    if ($parentId -le 0 -or -not $allById.ContainsKey($parentId)) { break }
    $parent = $allById[$parentId]
    if ([string]$parent.CommandLine -notmatch $rootPattern) { break }
    if ($ancestorIds.Add($parentId)) { $current = $parentId } else { break }
  }
}
$treeIds = @($treeIds + $ancestorIds | Select-Object -Unique)
foreach ($processId in ($treeIds | Sort-Object -Descending)) {
  Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}
Write-Host ("Stopped AI Lead Radar processes: " + (($treeIds | Sort-Object) -join ', '))

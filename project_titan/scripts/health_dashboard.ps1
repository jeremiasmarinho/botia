param(
  [string]$ReportDir = "reports",
  [int]$MaxEntries = 50,
  [switch]$Json,
  [switch]$SaveDashboard,
  [string]$LatestFileName = "health_dashboard_latest.json"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

if ([System.IO.Path]::IsPathRooted($ReportDir)) {
  $resolvedReportDir = $ReportDir
}
else {
  $resolvedReportDir = Join-Path $projectRoot $ReportDir
}

if (-not (Test-Path $resolvedReportDir)) {
  throw "Diretório de reports não encontrado: $resolvedReportDir"
}

# ── Collect smoke_health history files ──────────────────────────
$healthFiles = Get-ChildItem -Path $resolvedReportDir -Filter "smoke_health_*.json" -File -ErrorAction SilentlyContinue |
Where-Object { $_.Name -ne "smoke_health_latest.json" } |
Sort-Object Name |
Select-Object -Last $MaxEntries

if ($null -eq $healthFiles -or @($healthFiles).Count -eq 0) {
  $emptyDashboard = [PSCustomObject]@{
    generated_at     = (Get-Date).ToString("o")
    report_dir       = $resolvedReportDir
    entries_analyzed = 0
    date_range       = $null
    overall          = $null
    per_check        = $null
    vision_trend     = $null
    history          = @()
  }

  if ($SaveDashboard) {
    $latestPath = Join-Path $resolvedReportDir $LatestFileName
    $emptyDashboard | ConvertTo-Json -Depth 10 | Set-Content -Path $latestPath -Encoding UTF8
  }

  if ($Json) {
    $emptyDashboard | ConvertTo-Json -Depth 10
  }
  else {
    Write-Host "[DASHBOARD] Nenhum arquivo smoke_health_*.json encontrado em $resolvedReportDir"
  }
  exit 0
}

# ── Parse all entries ────────────────────────────────────────────
$entries = @()
foreach ($f in $healthFiles) {
  try {
    $raw = Get-Content -Path $f.FullName -Raw | ConvertFrom-Json
    $entries += [PSCustomObject]@{
      file           = $f.FullName
      generated_at   = [string]$raw.generated_at
      overall_status = [string]$raw.overall_status
      baseline       = [string]$raw.checks.baseline.status
      sweep          = [string]$raw.checks.sweep.status
      vision_profile = [string]$raw.checks.vision_profile.status
      vision_compare = [string]$raw.checks.vision_profile.compare_status
      squad          = [string]$raw.checks.squad.status
      training       = [string]$raw.checks.training.status
    }
  }
  catch {
    # Skip malformed files
  }
}

$totalEntries = @($entries).Count
if ($totalEntries -eq 0) {
  Write-Host "[DASHBOARD] Nenhum entry válido encontrado."
  exit 0
}

# ── Overall stats ────────────────────────────────────────────────
$passCount = @($entries | Where-Object { $_.overall_status -eq "pass" }).Count
$failCount = @($entries | Where-Object { $_.overall_status -eq "fail" }).Count
$unknownCount = $totalEntries - $passCount - $failCount
$passRatePct = [math]::Round(($passCount / $totalEntries) * 100, 1)

# ── Current streak (chronological order, last entry = most recent) ──
$streakStatus = $entries[-1].overall_status
$streakCount = 0
for ($i = $totalEntries - 1; $i -ge 0; $i--) {
  if ($entries[$i].overall_status -eq $streakStatus) {
    $streakCount++
  }
  else {
    break
  }
}

$overall = [PSCustomObject]@{
  pass_count     = $passCount
  fail_count     = $failCount
  unknown_count  = $unknownCount
  pass_rate_pct  = $passRatePct
  current_streak = [PSCustomObject]@{
    status = $streakStatus
    count  = $streakCount
  }
}

# ── Per-check pass rates ────────────────────────────────────────
function Get-CheckStats {
  param([string[]]$Values)

  $total = $Values.Count
  $pass = @($Values | Where-Object { $_ -eq "pass" }).Count
  $fail = @($Values | Where-Object { $_ -eq "fail" }).Count
  $rate = if ($total -gt 0) { [math]::Round(($pass / $total) * 100, 1) } else { 0 }

  return [PSCustomObject]@{
    pass_count    = $pass
    fail_count    = $fail
    unknown_count = $total - $pass - $fail
    pass_rate_pct = $rate
  }
}

$perCheck = [PSCustomObject]@{
  baseline       = Get-CheckStats -Values ($entries | ForEach-Object { $_.baseline })
  sweep          = Get-CheckStats -Values ($entries | ForEach-Object { $_.sweep })
  vision_profile = Get-CheckStats -Values ($entries | ForEach-Object { $_.vision_profile })
  vision_compare = Get-CheckStats -Values ($entries | ForEach-Object { $_.vision_compare })
  squad          = Get-CheckStats -Values ($entries | ForEach-Object { $_.squad })
  training       = Get-CheckStats -Values ($entries | ForEach-Object { $_.training })
}

# ── Vision trend (from vision_profile_compare files) ────────────
$visionCompareFiles = Get-ChildItem -Path $resolvedReportDir -Filter "vision_profile_compare_*.json" -File -ErrorAction SilentlyContinue |
Where-Object { $_.Name -ne "vision_profile_compare_latest.json" } |
Sort-Object Name |
Select-Object -Last $MaxEntries

$visionTrend = $null
if ($null -ne $visionCompareFiles -and @($visionCompareFiles).Count -gt 0) {
  $vp95 = @()
  $vfps = @()
  $vavg = @()

  foreach ($vcf in $visionCompareFiles) {
    try {
      $vc = Get-Content -Path $vcf.FullName -Raw | ConvertFrom-Json
      if ($null -ne $vc.latest) {
        if ($null -ne $vc.latest.latency_ms_p95) { $vp95 += [double]$vc.latest.latency_ms_p95 }
        if ($null -ne $vc.latest.achieved_fps) { $vfps += [double]$vc.latest.achieved_fps }
        if ($null -ne $vc.latest.latency_ms_avg) { $vavg += [double]$vc.latest.latency_ms_avg }
      }
    }
    catch { }
  }

  if ($vp95.Count -gt 0) {
    $p95First = $vp95[0]
    $p95Last = $vp95[-1]
    $p95Delta = if ($p95First -gt 0) { [math]::Round((($p95Last - $p95First) / $p95First) * 100, 2) } else { 0 }

    $fpsFirst = if ($vfps.Count -gt 0) { $vfps[0] } else { 0 }
    $fpsLast = if ($vfps.Count -gt 0) { $vfps[-1] } else { 0 }
    $fpsDelta = if ($fpsFirst -gt 0) { [math]::Round((($fpsLast - $fpsFirst) / $fpsFirst) * 100, 2) } else { 0 }

    $avgFirst = if ($vavg.Count -gt 0) { $vavg[0] } else { 0 }
    $avgLast = if ($vavg.Count -gt 0) { $vavg[-1] } else { 0 }
    $avgDelta = if ($avgFirst -gt 0) { [math]::Round((($avgLast - $avgFirst) / $avgFirst) * 100, 2) } else { 0 }

    $visionTrend = [PSCustomObject]@{
      entries = $vp95.Count
      p95_ms  = [PSCustomObject]@{
        first     = [math]::Round($p95First, 4)
        last      = [math]::Round($p95Last, 4)
        min       = [math]::Round(($vp95 | Measure-Object -Minimum).Minimum, 4)
        max       = [math]::Round(($vp95 | Measure-Object -Maximum).Maximum, 4)
        delta_pct = $p95Delta
      }
      avg_ms  = [PSCustomObject]@{
        first     = [math]::Round($avgFirst, 5)
        last      = [math]::Round($avgLast, 5)
        min       = if ($vavg.Count -gt 0) { [math]::Round(($vavg | Measure-Object -Minimum).Minimum, 5) } else { 0 }
        max       = if ($vavg.Count -gt 0) { [math]::Round(($vavg | Measure-Object -Maximum).Maximum, 5) } else { 0 }
        delta_pct = $avgDelta
      }
      fps     = [PSCustomObject]@{
        first     = [math]::Round($fpsFirst, 2)
        last      = [math]::Round($fpsLast, 2)
        min       = if ($vfps.Count -gt 0) { [math]::Round(($vfps | Measure-Object -Minimum).Minimum, 2) } else { 0 }
        max       = if ($vfps.Count -gt 0) { [math]::Round(($vfps | Measure-Object -Maximum).Maximum, 2) } else { 0 }
        delta_pct = $fpsDelta
      }
    }
  }
}

# ── Date range ───────────────────────────────────────────────────
$dateRange = [PSCustomObject]@{
  from = $entries[0].generated_at
  to   = $entries[-1].generated_at
}

# ── History (compact) ────────────────────────────────────────────
$compactHistory = @()
foreach ($e in $entries) {
  $compactHistory += [PSCustomObject]@{
    generated_at   = $e.generated_at
    overall_status = $e.overall_status
    baseline       = $e.baseline
    sweep          = $e.sweep
    vision_profile = $e.vision_profile
    vision_compare = $e.vision_compare
    squad          = $e.squad
    training       = $e.training
  }
}

# ── Assemble dashboard payload ─────────────────────────────────
$dashboard = [PSCustomObject]@{
  generated_at     = (Get-Date).ToString("o")
  report_dir       = $resolvedReportDir
  entries_analyzed = $totalEntries
  date_range       = $dateRange
  overall          = $overall
  per_check        = $perCheck
  vision_trend     = $visionTrend
  history          = $compactHistory
}

# ── Save files ──────────────────────────────────────────────────
if ($SaveDashboard) {
  $latestPath = Join-Path $resolvedReportDir $LatestFileName
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $historyPath = Join-Path $resolvedReportDir "health_dashboard_$stamp.json"

  $dashboard | ConvertTo-Json -Depth 10 | Set-Content -Path $latestPath -Encoding UTF8
  $dashboard | ConvertTo-Json -Depth 10 | Set-Content -Path $historyPath -Encoding UTF8
}

# ── Output ──────────────────────────────────────────────────────
if ($Json) {
  $dashboard | ConvertTo-Json -Depth 10
}
else {
  # Pre-compute display strings to avoid PowerShell parse issues with % and nested quotes
  $dEntries  = $totalEntries.ToString().PadLeft(5)
  $dRate     = $passRatePct.ToString('0.0').PadLeft(5)
  $dStreak   = $streakCount.ToString().PadLeft(3)
  $dStreakSt  = $streakStatus.PadRight(8)

  $bP = $perCheck.baseline.pass_count.ToString().PadLeft(4)
  $bF = $perCheck.baseline.fail_count.ToString().PadLeft(4)
  $bR = $perCheck.baseline.pass_rate_pct.ToString('0.0').PadLeft(5)
  $sP = $perCheck.sweep.pass_count.ToString().PadLeft(4)
  $sF = $perCheck.sweep.fail_count.ToString().PadLeft(4)
  $sR = $perCheck.sweep.pass_rate_pct.ToString('0.0').PadLeft(5)
  $vpP = $perCheck.vision_profile.pass_count.ToString().PadLeft(4)
  $vpF = $perCheck.vision_profile.fail_count.ToString().PadLeft(4)
  $vpR = $perCheck.vision_profile.pass_rate_pct.ToString('0.0').PadLeft(5)
  $vcP = $perCheck.vision_compare.pass_count.ToString().PadLeft(4)
  $vcF = $perCheck.vision_compare.fail_count.ToString().PadLeft(4)
  $vcR = $perCheck.vision_compare.pass_rate_pct.ToString('0.0').PadLeft(5)
  $sqP = $perCheck.squad.pass_count.ToString().PadLeft(4)
  $sqF = $perCheck.squad.fail_count.ToString().PadLeft(4)
  $sqR = $perCheck.squad.pass_rate_pct.ToString('0.0').PadLeft(5)
  $trP = $perCheck.training.pass_count.ToString().PadLeft(4)
  $trF = $perCheck.training.fail_count.ToString().PadLeft(4)
  $trR = $perCheck.training.pass_rate_pct.ToString('0.0').PadLeft(5)

  Write-Host ''
  Write-Host '======================================================'
  Write-Host '       PROJECT TITAN - HEALTH DASHBOARD'
  Write-Host '======================================================'
  Write-Host "  Entries analyzed : $dEntries"
  Write-Host "  Pass rate        : $dRate pct"
  Write-Host "  Current streak   : ${dStreak}x $dStreakSt"
  Write-Host '------------------------------------------------------'
  Write-Host '  CHECK            PASS  FAIL  RATE'
  Write-Host "  baseline         $bP  $bF  $bR pct"
  Write-Host "  sweep            $sP  $sF  $sR pct"
  Write-Host "  vision_profile   $vpP  $vpF  $vpR pct"
  Write-Host "  vision_compare   $vcP  $vcF  $vcR pct"
  Write-Host "  squad            $sqP  $sqF  $sqR pct"
  Write-Host "  training         $trP  $trF  $trR pct"

  if ($null -ne $visionTrend) {
    $p95First = $visionTrend.p95_ms.first.ToString('0.0000')
    $p95Last  = $visionTrend.p95_ms.last.ToString('0.0000')
    $p95D     = $visionTrend.p95_ms.delta_pct.ToString('+0.0;-0.0')
    $avgFirst = $visionTrend.avg_ms.first.ToString('0.00000')
    $avgLast  = $visionTrend.avg_ms.last.ToString('0.00000')
    $avgD     = $visionTrend.avg_ms.delta_pct.ToString('+0.0;-0.0')
    $fpsFirst = $visionTrend.fps.first.ToString('0.00')
    $fpsLast  = $visionTrend.fps.last.ToString('0.00')
    $fpsD     = $visionTrend.fps.delta_pct.ToString('+0.0;-0.0')

    Write-Host '------------------------------------------------------'
    Write-Host "  VISION TREND     ($($visionTrend.entries) samples)"
    Write-Host "  p95 latency (ms) : $p95First -> $p95Last (${p95D} pct)"
    Write-Host "  avg latency (ms) : $avgFirst -> $avgLast (${avgD} pct)"
    Write-Host "  achieved FPS     : $fpsFirst -> $fpsLast (${fpsD} pct)"
  }

  Write-Host '======================================================'
  Write-Host ''

  if ($SaveDashboard) {
    Write-Host "[DASHBOARD] latest_file=$latestPath"
    Write-Host "[DASHBOARD] history_file=$historyPath"
  }
}

exit 0

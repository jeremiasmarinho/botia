param(
  [string]$ReportDir = "reports"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

if ([System.IO.Path]::IsPathRooted($ReportDir)) {
  $resolvedReportDir = $ReportDir
}
else {
  $resolvedReportDir = Join-Path $projectRoot $ReportDir
}

Write-Host "[SMOKE-DASHBOARD] Validando health_dashboard.ps1 ..."

# ── Run dashboard with JSON + SaveDashboard ─────────────────────
$dashboardScript = Join-Path $PSScriptRoot "health_dashboard.ps1"
if (-not (Test-Path $dashboardScript)) {
  throw "Script não encontrado: $dashboardScript"
}

$output = & powershell -ExecutionPolicy Bypass -File $dashboardScript -ReportDir $resolvedReportDir -Json -SaveDashboard -MaxEntries 50 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
  throw "health_dashboard.ps1 falhou (exit=$LASTEXITCODE)`n$output"
}

# ── Parse JSON output ───────────────────────────────────────────
$dashboard = $null
try {
  $dashboard = $output | ConvertFrom-Json
}
catch {
  throw "Output não é JSON válido:`n$output"
}

# ── Validate required fields ────────────────────────────────────
$requiredFields = @("generated_at", "report_dir", "entries_analyzed", "overall", "per_check", "history")
foreach ($field in $requiredFields) {
  $val = $dashboard.PSObject.Properties[$field]
  if ($null -eq $val) {
    throw "Campo obrigatório ausente: $field"
  }
}

Write-Host "[SMOKE-DASHBOARD]   generated_at     = $($dashboard.generated_at)"
Write-Host "[SMOKE-DASHBOARD]   entries_analyzed  = $($dashboard.entries_analyzed)"

# ── Validate overall block ──────────────────────────────────────
if ($dashboard.entries_analyzed -gt 0) {
  $overallFields = @("pass_count", "fail_count", "unknown_count", "pass_rate_pct", "current_streak")
  foreach ($field in $overallFields) {
    $val = $dashboard.overall.PSObject.Properties[$field]
    if ($null -eq $val) {
      throw "Campo overall.$field ausente"
    }
  }

  if ($null -eq $dashboard.overall.current_streak.status -or
      $null -eq $dashboard.overall.current_streak.count) {
    throw "Campo overall.current_streak incompleto"
  }

  Write-Host "[SMOKE-DASHBOARD]   pass_rate_pct    = $($dashboard.overall.pass_rate_pct)%"
  Write-Host "[SMOKE-DASHBOARD]   current_streak   = $($dashboard.overall.current_streak.count)x $($dashboard.overall.current_streak.status)"

  # ── Validate per_check block ─────────────────────────────────
  $checkNames = @("baseline", "sweep", "vision_profile", "vision_compare", "squad")
  foreach ($cn in $checkNames) {
    $check = $dashboard.per_check.PSObject.Properties[$cn]
    if ($null -eq $check) {
      throw "Campo per_check.$cn ausente"
    }
    $checkVal = $check.Value
    foreach ($sf in @("pass_count", "fail_count", "pass_rate_pct")) {
      if ($null -eq $checkVal.PSObject.Properties[$sf]) {
        throw "Campo per_check.$cn.$sf ausente"
      }
    }
  }
  Write-Host "[SMOKE-DASHBOARD]   per_check        = OK (5 checks)"

  # ── Validate history array ────────────────────────────────────
  $historyCount = @($dashboard.history).Count
  if ($historyCount -ne $dashboard.entries_analyzed) {
    throw "history.Count ($historyCount) != entries_analyzed ($($dashboard.entries_analyzed))"
  }
  Write-Host "[SMOKE-DASHBOARD]   history          = $historyCount entries"

  # ── Validate date_range ───────────────────────────────────────
  if ($null -eq $dashboard.date_range -or
      $null -eq $dashboard.date_range.from -or
      $null -eq $dashboard.date_range.to) {
    throw "Campo date_range incompleto"
  }
  Write-Host "[SMOKE-DASHBOARD]   date_range       = $($dashboard.date_range.from) → $($dashboard.date_range.to)"
}

# ── Validate latest file was saved ──────────────────────────────
$latestFile = Join-Path $resolvedReportDir "health_dashboard_latest.json"
if (-not (Test-Path $latestFile)) {
  throw "Arquivo estável não encontrado: $latestFile"
}
Write-Host "[SMOKE-DASHBOARD]   latest_file      = $latestFile"

# ── Validate the saved file is valid JSON ───────────────────────
try {
  $saved = Get-Content -Path $latestFile -Raw | ConvertFrom-Json
  if ($null -eq $saved.generated_at) {
    throw "Arquivo salvo inválido: generated_at ausente"
  }
}
catch {
  throw "Arquivo salvo não é JSON válido: $latestFile"
}

Write-Host "[SMOKE-DASHBOARD] OK - dashboard validado com sucesso."
exit 0

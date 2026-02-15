param(
  [switch]$HealthOnly,
  [ValidateSet("off", "wait", "fold", "call", "raise", "cycle")]
  [string]$SimScenario = "off",
  [ValidateRange(0, 100000)]
  [int]$Ticks = 0,
  [ValidateRange(0.05, 10.0)]
  [double]$TickSeconds = 0.2,
  [string]$ReportDir = "",
  [switch]$OpenLastReport,
  [switch]$PrintLastReport,
  [string]$LabelMapFile = "",
  [ValidateSet("tight", "normal", "aggressive")]
  [string]$TableProfile = "normal",
  [ValidateSet("utg", "mp", "co", "btn", "sb", "bb")]
  [string]$TablePosition = "mp",
  [ValidateRange(1, 9)]
  [int]$Opponents = 1,
  [ValidateRange(100, 100000)]
  [int]$Simulations = 10000,
  [switch]$DynamicSimulations,
  [switch]$ProfileSweep,
  [switch]$PositionSweep,
  [switch]$CompareSweepHistory,
  [switch]$OnlySweepHistory,
  [switch]$SweepDashboard,
  [switch]$SaveHistoryCompare,
  [ValidateSet("profile", "position")]
  [string]$SweepHistoryMode = "profile",
  [ValidateRange(2, 20)]
  [int]$HistoryDepth = 5
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = $null
$offlineMode = $OnlySweepHistory -or $SweepDashboard

if (-not $offlineMode) {
  $pythonCandidates = @(
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    (Join-Path (Split-Path -Parent $projectRoot) ".venv\Scripts\python.exe")
  )

  foreach ($candidate in $pythonCandidates) {
    if (Test-Path $candidate) {
      $pythonExe = $candidate
      break
    }
  }

  if (-not $pythonExe) {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
      $pythonExe = $pythonCmd.Source
    }
  }

  if (-not $pythonExe) {
    throw "Python não encontrado. Crie/ative um ambiente virtual e instale as dependências."
  }
}

Push-Location $projectRoot
$_previousSimScenario = $env:TITAN_SIM_SCENARIO
$_previousMaxTicks = $env:TITAN_MAX_TICKS
$_previousTickSeconds = $env:TITAN_TICK_SECONDS
$_previousReportDir = $env:TITAN_REPORT_DIR
$_previousLabelMapFile = $env:TITAN_VISION_LABEL_MAP_FILE
$_previousTableProfile = $env:TITAN_TABLE_PROFILE
$_previousTablePosition = $env:TITAN_TABLE_POSITION
$_previousOpponents = $env:TITAN_OPPONENTS
$_previousSimulations = $env:TITAN_SIMULATIONS
$_previousDynamicSimulations = $env:TITAN_DYNAMIC_SIMULATIONS
$resolvedReportDir = $null

function Get-LatestRunReport {
  param([string]$Directory)

  $latestReport = Get-ChildItem -Path $Directory -Filter run_report_*.json -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

  if ($null -eq $latestReport) {
    return $null
  }

  try {
    $content = Get-Content -Path $latestReport.FullName -Raw
    $json = $content | ConvertFrom-Json
    return [PSCustomObject]@{
      File = $latestReport.FullName
      Json = $json

    }
  }
  catch {
    Write-Warning "Falha ao ler relatório JSON: $($latestReport.FullName)"
    return $null
  }
}

function Show-SweepRanking {
  param(
    [array]$Results,
    [string]$Dimension
  )

  if ($null -eq $Results -or $Results.Count -eq 0) {
    return
  }

  $ranked = $Results |
  Sort-Object @{
    Expression = { [double]$_.score }
    Descending = $true
  }, @{
    Expression = { [double]$_.average_win_rate }
    Descending = $true
  }, @{
    Expression = { [int]$_.raises }
    Descending = $true
  }, @{
    Expression = { [int]$_.folds }
    Descending = $false
  }

  $best = $ranked | Select-Object -First 1
  $worst = $ranked | Select-Object -Last 1

  if ($null -eq $best -or $null -eq $worst) {
    return
  }

  $bestName = $best.$Dimension
  $worstName = $worst.$Dimension

  Write-Host "[RUN] Best ${Dimension}: $bestName (score=$([math]::Round([double]$best.score, 4)) win_rate=$([math]::Round([double]$best.average_win_rate, 4)) raises=$([int]$best.raises) folds=$([int]$best.folds))"
  Write-Host "[RUN] Worst ${Dimension}: $worstName (score=$([math]::Round([double]$worst.score, 4)) win_rate=$([math]::Round([double]$worst.average_win_rate, 4)) raises=$([int]$worst.raises) folds=$([int]$worst.folds))"

  return [PSCustomObject]@{
    dimension = $Dimension
    best      = $best
    worst     = $worst
  }
}

function Get-SweepScore {
  param(
    [double]$AverageWinRate,
    [int]$Outcomes,
    [int]$Raises,
    [int]$Folds
  )

  $safeOutcomes = [math]::Max($Outcomes, 1)
  $raiseRate = [double]$Raises / $safeOutcomes
  $foldRate = [double]$Folds / $safeOutcomes

  $score = $AverageWinRate + (0.05 * $raiseRate) - (0.03 * $foldRate)
  return [math]::Round($score, 6)
}

function Write-SweepSummary {
  param(
    [string]$Directory,
    [string]$Mode,
    [array]$Results,
    [object]$Ranking,
    [hashtable]$Settings
  )

  if ([string]::IsNullOrWhiteSpace($Directory) -or [string]::IsNullOrWhiteSpace($Mode) -or $null -eq $Results -or $Results.Count -eq 0) {
    return $null
  }

  try {
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
    $fileName = "sweep_summary_${Mode}_$timestamp.json"
    $filePath = Join-Path $Directory $fileName

    $payload = [PSCustomObject]@{
      generated_at = (Get-Date).ToString("o")
      mode         = $Mode
      settings     = [PSCustomObject]$Settings
      ranking      = $Ranking
      results      = $Results
    }

    $json = $payload | ConvertTo-Json -Depth 8
    Set-Content -Path $filePath -Value $json -Encoding UTF8
    return $filePath
  }
  catch {
    Write-Warning "Falha ao salvar sweep summary JSON: $($_.Exception.Message)"
    return $null
  }
}

function Show-SweepHistoryComparison {
  param(
    [string]$Directory,
    [string]$Mode,
    [string]$Dimension,
    [int]$Depth,
    [switch]$SaveOutput
  )

  if ([string]::IsNullOrWhiteSpace($Directory) -or [string]::IsNullOrWhiteSpace($Mode) -or [string]::IsNullOrWhiteSpace($Dimension)) {
    return
  }

  $summaryFiles = Get-ChildItem -Path $Directory -Filter "sweep_summary_${Mode}_*.json" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First $Depth

  if ($null -eq $summaryFiles -or $summaryFiles.Count -lt 2) {
    Write-Warning "Histórico insuficiente para comparação de sweep '$Mode' (mínimo 2 arquivos)."
    return
  }

  try {
    $latestJson = Get-Content -Path $summaryFiles[0].FullName -Raw | ConvertFrom-Json
    $previousJson = Get-Content -Path $summaryFiles[1].FullName -Raw | ConvertFrom-Json

    if ($null -eq $latestJson.results -or $null -eq $previousJson.results) {
      Write-Warning "Sweep summary sem campo 'results' para comparação."
      return
    }

    $previousByItem = @{}
    foreach ($row in $previousJson.results) {
      $itemName = [string]$row.$Dimension
      if (-not [string]::IsNullOrWhiteSpace($itemName)) {
        $previousByItem[$itemName] = $row
      }
    }

    $comparisonRows = @()
    foreach ($currentRow in $latestJson.results) {
      $itemName = [string]$currentRow.$Dimension
      if ([string]::IsNullOrWhiteSpace($itemName)) {
        continue
      }

      $previousRow = $null
      if ($previousByItem.ContainsKey($itemName)) {
        $previousRow = $previousByItem[$itemName]
      }

      $currentScore = [double]$currentRow.score
      $currentWinRate = [double]$currentRow.average_win_rate
      $previousScore = $null
      $previousWinRate = $null
      $deltaScore = $null
      $deltaWinRate = $null

      if ($null -ne $previousRow) {
        $previousScore = [double]$previousRow.score
        $previousWinRate = [double]$previousRow.average_win_rate
        $deltaScore = [math]::Round($currentScore - $previousScore, 6)
        $deltaWinRate = [math]::Round($currentWinRate - $previousWinRate, 6)
      }

      $comparisonRows += [PSCustomObject]@{
        item           = $itemName
        score          = [math]::Round($currentScore, 6)
        prev_score     = $previousScore
        delta_score    = $deltaScore
        win_rate       = [math]::Round($currentWinRate, 6)
        prev_win_rate  = $previousWinRate
        delta_win_rate = $deltaWinRate
      }
    }

    if ($comparisonRows.Count -eq 0) {
      return
    }

    Write-Host "[RUN] Sweep history compare ($Mode): latest vs previous"
    if ($Dimension -eq "position") {
      $positionOrder = @{ utg = 0; mp = 1; co = 2; btn = 3; sb = 4; bb = 5 }
      $comparisonRows |
      Sort-Object { $positionOrder[$_.item] } |
      Format-Table item, score, prev_score, delta_score, win_rate, prev_win_rate, delta_win_rate -AutoSize
    }
    else {
      $comparisonRows |
      Sort-Object item |
      Format-Table item, score, prev_score, delta_score, win_rate, prev_win_rate, delta_win_rate -AutoSize
    }

    if ($SaveOutput) {
      $timestamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
      $filePath = Join-Path $Directory "history_compare_${Mode}_$timestamp.json"

      $payload = [PSCustomObject]@{
        generated_at  = (Get-Date).ToString("o")
        mode          = $Mode
        depth         = $Depth
        latest_file   = $summaryFiles[0].FullName
        previous_file = $summaryFiles[1].FullName
        rows          = $comparisonRows
      }

      $json = $payload | ConvertTo-Json -Depth 8
      Set-Content -Path $filePath -Value $json -Encoding UTF8
      Write-Host "[RUN] History compare file: $filePath"
    }
  }
  catch {
    Write-Warning "Falha ao comparar histórico de sweep: $($_.Exception.Message)"
  }
}

function Show-SweepDashboard {
  param(
    [string]$Directory,
    [int]$Depth
  )

  if ([string]::IsNullOrWhiteSpace($Directory)) {
    return
  }

  $modes = @("profile", "position")
  $dashboardRows = @()

  foreach ($mode in $modes) {
    $summaryFiles = Get-ChildItem -Path $Directory -Filter "sweep_summary_${mode}_*.json" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First $Depth

    foreach ($summaryFile in $summaryFiles) {
      try {
        $summary = Get-Content -Path $summaryFile.FullName -Raw | ConvertFrom-Json
        if ($null -eq $summary.ranking -or $null -eq $summary.ranking.best -or $null -eq $summary.ranking.worst) {
          continue
        }

        $dimension = if ($mode -eq "position") { "position" } else { "profile" }
        $bestName = [string]$summary.ranking.best.$dimension
        $worstName = [string]$summary.ranking.worst.$dimension

        $dashboardRows += [PSCustomObject]@{
          mode         = $mode
          generated_at = [string]$summary.generated_at
          best_item    = $bestName
          best_score   = [double]$summary.ranking.best.score
          worst_item   = $worstName
          worst_score  = [double]$summary.ranking.worst.score
          file         = $summaryFile.Name
        }
      }
      catch {
        Write-Warning "Falha ao ler sweep summary para dashboard: $($summaryFile.FullName)"
      }
    }
  }

  if ($dashboardRows.Count -eq 0) {
    Write-Warning "Nenhum sweep summary encontrado para dashboard em: $Directory"
    return
  }

  Write-Host "[RUN] Sweep dashboard (últimos $Depth por modo)"
  $dashboardRows |
  Sort-Object mode, @{Expression = { [datetime]$_.generated_at }; Descending = $true } |
  Format-Table mode, generated_at, best_item, best_score, worst_item, worst_score -AutoSize
}

try {
  if ($SimScenario -ne "off") {
    $env:TITAN_SIM_SCENARIO = $SimScenario
    Write-Host "[SIM] TITAN_SIM_SCENARIO=$SimScenario"
  }

  if ($Ticks -gt 0) {
    $env:TITAN_MAX_TICKS = "$Ticks"
    Write-Host "[RUN] TITAN_MAX_TICKS=$Ticks"
  }

  $env:TITAN_TICK_SECONDS = "$TickSeconds"
  Write-Host "[RUN] TITAN_TICK_SECONDS=$TickSeconds"

  $env:TITAN_TABLE_PROFILE = "$TableProfile"
  Write-Host "[RUN] TITAN_TABLE_PROFILE=$TableProfile"

  $env:TITAN_TABLE_POSITION = "$TablePosition"
  Write-Host "[RUN] TITAN_TABLE_POSITION=$TablePosition"

  $env:TITAN_OPPONENTS = "$Opponents"
  Write-Host "[RUN] TITAN_OPPONENTS=$Opponents"

  $env:TITAN_SIMULATIONS = "$Simulations"
  Write-Host "[RUN] TITAN_SIMULATIONS=$Simulations"

  $env:TITAN_DYNAMIC_SIMULATIONS = "0"
  if ($DynamicSimulations) {
    $env:TITAN_DYNAMIC_SIMULATIONS = "1"
  }
  Write-Host "[RUN] TITAN_DYNAMIC_SIMULATIONS=$($env:TITAN_DYNAMIC_SIMULATIONS)"

  if (-not [string]::IsNullOrWhiteSpace($LabelMapFile)) {
    if ([System.IO.Path]::IsPathRooted($LabelMapFile)) {
      $resolvedLabelMapFile = $LabelMapFile
    }
    else {
      $resolvedLabelMapFile = Join-Path $projectRoot $LabelMapFile
    }
    $env:TITAN_VISION_LABEL_MAP_FILE = "$resolvedLabelMapFile"
    Write-Host "[RUN] TITAN_VISION_LABEL_MAP_FILE=$resolvedLabelMapFile"
  }

  if (-not [string]::IsNullOrWhiteSpace($ReportDir)) {
    if ([System.IO.Path]::IsPathRooted($ReportDir)) {
      $resolvedReportDir = $ReportDir
    }
    else {
      $resolvedReportDir = Join-Path $projectRoot $ReportDir
    }
  }
  elseif (($OpenLastReport -or $PrintLastReport) -and -not $HealthOnly) {
    $resolvedReportDir = Join-Path $projectRoot "reports"
  }

  if (($ProfileSweep -or $PositionSweep) -and -not $HealthOnly -and $null -eq $resolvedReportDir) {
    $resolvedReportDir = Join-Path $projectRoot "reports"
  }

  if ($OnlySweepHistory -and $null -eq $resolvedReportDir) {
    $resolvedReportDir = Join-Path $projectRoot "reports"
  }

  if ($SweepDashboard -and $null -eq $resolvedReportDir) {
    $resolvedReportDir = Join-Path $projectRoot "reports"
  }

  if ($null -ne $resolvedReportDir) {
    $env:TITAN_REPORT_DIR = "$resolvedReportDir"
    Write-Host "[RUN] TITAN_REPORT_DIR=$resolvedReportDir"
  }

  if ($OnlySweepHistory) {
    $dimension = if ($SweepHistoryMode -eq "position") { "position" } else { "profile" }
    Show-SweepHistoryComparison -Directory $resolvedReportDir -Mode $SweepHistoryMode -Dimension $dimension -Depth $HistoryDepth -SaveOutput:$SaveHistoryCompare
    exit 0
  }

  if ($SweepDashboard) {
    Show-SweepDashboard -Directory $resolvedReportDir -Depth $HistoryDepth
    exit 0
  }

  Write-Host "[1/2] Running healthcheck with: $pythonExe"
  & $pythonExe -m orchestrator.healthcheck

  if ($LASTEXITCODE -ne 0) {
    throw "Healthcheck falhou com código $LASTEXITCODE"
  }

  if ($HealthOnly) {
    Write-Host "Healthcheck OK. Encerrando por -HealthOnly."
    exit 0
  }

  if ($ProfileSweep -and $PositionSweep) {
    throw "Use apenas um modo de sweep por execução: -ProfileSweep ou -PositionSweep."
  }

  if ($ProfileSweep -or $PositionSweep) {
    if ($Ticks -le 0) {
      Write-Warning "Sweep sem -Ticks pode rodar indefinidamente. Use -Ticks para benchmark curto."
    }

    if ($null -eq $resolvedReportDir) {
      throw "Não foi possível definir diretório de relatório para sweep."
    }

    $profiles = @("tight", "normal", "aggressive")
    $positions = @("utg", "mp", "co", "btn", "sb", "bb")
    $sweepResults = @()

    if ($ProfileSweep) {
      foreach ($profile in $profiles) {
        Write-Host "[2/2] Profile sweep run: $profile"
        $env:TITAN_TABLE_PROFILE = $profile

        & $pythonExe -m orchestrator.engine
        if ($LASTEXITCODE -ne 0) {
          throw "Engine finalizou com código $LASTEXITCODE no profile '$profile'"
        }

        $latestReportData = Get-LatestRunReport -Directory $resolvedReportDir
        if ($null -eq $latestReportData) {
          Write-Warning "Relatório não encontrado para profile '$profile'."
          continue
        }

        $reportJson = $latestReportData.Json
        $actionCounts = $reportJson.action_counts
        $foldCount = 0
        $callCount = 0
        $raiseCount = 0

        if ($null -ne $actionCounts) {
          if ($null -ne $actionCounts.fold) { $foldCount = [int]$actionCounts.fold }
          if ($null -ne $actionCounts.call) { $callCount = [int]$actionCounts.call }
          if ($null -ne $actionCounts.raise_small) { $raiseCount += [int]$actionCounts.raise_small }
          if ($null -ne $actionCounts.raise_big) { $raiseCount += [int]$actionCounts.raise_big }
        }

        $simUsage = $reportJson.simulation_usage
        $simAverage = $null
        if ($null -ne $simUsage -and $null -ne $simUsage.average) {
          $simAverage = [double]$simUsage.average
        }

        $sweepResults += [PSCustomObject]@{
          score              = (Get-SweepScore -AverageWinRate ([double]$reportJson.average_win_rate) -Outcomes ([int]$reportJson.outcomes) -Raises $raiseCount -Folds $foldCount)
          profile            = $profile
          outcomes           = [int]$reportJson.outcomes
          average_win_rate   = [double]$reportJson.average_win_rate
          folds              = $foldCount
          calls              = $callCount
          raises             = $raiseCount
          simulation_average = $simAverage
          report_file        = $latestReportData.File
        }
      }

      if ($sweepResults.Count -gt 0) {
        Write-Host "[RUN] Profile sweep summary"
        $sweepResults |
        Sort-Object profile |
        Format-Table profile, score, outcomes, average_win_rate, folds, calls, raises, simulation_average -AutoSize
        $ranking = Show-SweepRanking -Results $sweepResults -Dimension "profile"
        $summaryFile = Write-SweepSummary -Directory $resolvedReportDir -Mode "profile" -Results $sweepResults -Ranking $ranking -Settings @{
          sim_scenario        = $SimScenario
          ticks               = $Ticks
          tick_seconds        = $TickSeconds
          opponents           = $Opponents
          simulations         = $Simulations
          dynamic_simulations = [bool]$DynamicSimulations
          table_position      = $TablePosition
        }
        if ($null -ne $summaryFile) {
          Write-Host "[RUN] Sweep summary file: $summaryFile"
        }
        if ($CompareSweepHistory) {
          Show-SweepHistoryComparison -Directory $resolvedReportDir -Mode "profile" -Dimension "profile" -Depth $HistoryDepth -SaveOutput:$SaveHistoryCompare
        }
      }
    }
    else {
      foreach ($position in $positions) {
        Write-Host "[2/2] Position sweep run: $position"
        $env:TITAN_TABLE_POSITION = $position

        & $pythonExe -m orchestrator.engine
        if ($LASTEXITCODE -ne 0) {
          throw "Engine finalizou com código $LASTEXITCODE na position '$position'"
        }

        $latestReportData = Get-LatestRunReport -Directory $resolvedReportDir
        if ($null -eq $latestReportData) {
          Write-Warning "Relatório não encontrado para position '$position'."
          continue
        }

        $reportJson = $latestReportData.Json
        $actionCounts = $reportJson.action_counts
        $foldCount = 0
        $callCount = 0
        $raiseCount = 0

        if ($null -ne $actionCounts) {
          if ($null -ne $actionCounts.fold) { $foldCount = [int]$actionCounts.fold }
          if ($null -ne $actionCounts.call) { $callCount = [int]$actionCounts.call }
          if ($null -ne $actionCounts.raise_small) { $raiseCount += [int]$actionCounts.raise_small }
          if ($null -ne $actionCounts.raise_big) { $raiseCount += [int]$actionCounts.raise_big }
        }

        $simUsage = $reportJson.simulation_usage
        $simAverage = $null
        if ($null -ne $simUsage -and $null -ne $simUsage.average) {
          $simAverage = [double]$simUsage.average
        }

        $sweepResults += [PSCustomObject]@{
          score              = (Get-SweepScore -AverageWinRate ([double]$reportJson.average_win_rate) -Outcomes ([int]$reportJson.outcomes) -Raises $raiseCount -Folds $foldCount)
          position           = $position
          outcomes           = [int]$reportJson.outcomes
          average_win_rate   = [double]$reportJson.average_win_rate
          folds              = $foldCount
          calls              = $callCount
          raises             = $raiseCount
          simulation_average = $simAverage
          report_file        = $latestReportData.File
        }
      }

      if ($sweepResults.Count -gt 0) {
        Write-Host "[RUN] Position sweep summary"
        $positionOrder = @{ utg = 0; mp = 1; co = 2; btn = 3; sb = 4; bb = 5 }
        $sweepResults |
        Sort-Object { $positionOrder[$_.position] } |
        Format-Table position, score, outcomes, average_win_rate, folds, calls, raises, simulation_average -AutoSize
        $ranking = Show-SweepRanking -Results $sweepResults -Dimension "position"
        $summaryFile = Write-SweepSummary -Directory $resolvedReportDir -Mode "position" -Results $sweepResults -Ranking $ranking -Settings @{
          sim_scenario        = $SimScenario
          ticks               = $Ticks
          tick_seconds        = $TickSeconds
          opponents           = $Opponents
          simulations         = $Simulations
          dynamic_simulations = [bool]$DynamicSimulations
          table_profile       = $TableProfile
        }
        if ($null -ne $summaryFile) {
          Write-Host "[RUN] Sweep summary file: $summaryFile"
        }
        if ($CompareSweepHistory) {
          Show-SweepHistoryComparison -Directory $resolvedReportDir -Mode "position" -Dimension "position" -Depth $HistoryDepth -SaveOutput:$SaveHistoryCompare
        }
      }
    }
  }
  else {
    Write-Host "[2/2] Starting orchestrator engine... (Ctrl+C para parar)"
    & $pythonExe -m orchestrator.engine

    if ($LASTEXITCODE -ne 0) {
      throw "Engine finalizou com código $LASTEXITCODE"
    }
  }

  if (($OpenLastReport -or $PrintLastReport) -and $null -ne $resolvedReportDir) {
    $latestReport = Get-ChildItem -Path $resolvedReportDir -Filter run_report_*.json -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

    if ($null -ne $latestReport) {
      if ($PrintLastReport) {
        Write-Host "[RUN] Latest report file: $($latestReport.FullName)"
        Write-Host "[RUN] Latest report JSON:"
        Get-Content -Path $latestReport.FullName -Raw
      }

      if ($OpenLastReport) {
        try {
          Start-Process -FilePath $latestReport.FullName | Out-Null
          Write-Host "[RUN] Opened latest report: $($latestReport.FullName)"
        }
        catch {
          Write-Warning "Não foi possível abrir o relatório automaticamente: $($latestReport.FullName)"
        }
      }
    }
    else {
      Write-Warning "Nenhum relatório encontrado em: $resolvedReportDir"
    }
  }
}
finally {
  if ($null -eq $_previousSimScenario) {
    Remove-Item Env:TITAN_SIM_SCENARIO -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_SIM_SCENARIO = $_previousSimScenario
  }

  if ($null -eq $_previousMaxTicks) {
    Remove-Item Env:TITAN_MAX_TICKS -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_MAX_TICKS = $_previousMaxTicks
  }

  if ($null -eq $_previousTickSeconds) {
    Remove-Item Env:TITAN_TICK_SECONDS -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_TICK_SECONDS = $_previousTickSeconds
  }

  if ($null -eq $_previousReportDir) {
    Remove-Item Env:TITAN_REPORT_DIR -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_REPORT_DIR = $_previousReportDir
  }

  if ($null -eq $_previousLabelMapFile) {
    Remove-Item Env:TITAN_VISION_LABEL_MAP_FILE -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_VISION_LABEL_MAP_FILE = $_previousLabelMapFile
  }

  if ($null -eq $_previousTableProfile) {
    Remove-Item Env:TITAN_TABLE_PROFILE -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_TABLE_PROFILE = $_previousTableProfile
  }

  if ($null -eq $_previousTablePosition) {
    Remove-Item Env:TITAN_TABLE_POSITION -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_TABLE_POSITION = $_previousTablePosition
  }

  if ($null -eq $_previousOpponents) {
    Remove-Item Env:TITAN_OPPONENTS -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_OPPONENTS = $_previousOpponents
  }

  if ($null -eq $_previousSimulations) {
    Remove-Item Env:TITAN_SIMULATIONS -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_SIMULATIONS = $_previousSimulations
  }

  if ($null -eq $_previousDynamicSimulations) {
    Remove-Item Env:TITAN_DYNAMIC_SIMULATIONS -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_DYNAMIC_SIMULATIONS = $_previousDynamicSimulations
  }

  Pop-Location
}
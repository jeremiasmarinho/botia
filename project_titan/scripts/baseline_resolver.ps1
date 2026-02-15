function Get-TitanBestSweepItem {
  param(
    [string]$Directory,
    [string]$Mode,
    [string]$Dimension,
    [switch]$Quiet
  )

  if ([string]::IsNullOrWhiteSpace($Directory) -or [string]::IsNullOrWhiteSpace($Mode) -or [string]::IsNullOrWhiteSpace($Dimension)) {
    return $null
  }

  $summaryFile = Get-ChildItem -Path $Directory -Filter "sweep_summary_${Mode}_*.json" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

  if ($null -eq $summaryFile) {
    return $null
  }

  try {
    $summary = Get-Content -Path $summaryFile.FullName -Raw | ConvertFrom-Json
    if ($null -eq $summary -or $null -eq $summary.ranking -or $null -eq $summary.ranking.best) {
      return $null
    }

    $bestItem = [string]$summary.ranking.best.$Dimension
    if ([string]::IsNullOrWhiteSpace($bestItem)) {
      return $null
    }

    return [PSCustomObject]@{
      item = $bestItem
      file = $summaryFile.FullName
    }
  }
  catch {
    if (-not $Quiet) {
      Write-Warning "Falha ao ler baseline de sweep '$Mode': $($summaryFile.FullName)"
    }
    return $null
  }
}

function Resolve-TitanBaseline {
  param(
    [string]$Directory,
    [string]$FallbackProfile = "normal",
    [string]$FallbackPosition = "mp",
    [switch]$Quiet
  )

  $result = [PSCustomObject]@{
    table_profile   = $FallbackProfile
    table_position  = $FallbackPosition
    profile_source  = "manual/param"
    position_source = "manual/param"
    source          = "manual"
  }

  if ([string]::IsNullOrWhiteSpace($Directory)) {
    return $result
  }

  $baselineFile = Join-Path $Directory "baseline_best.json"
  if (Test-Path $baselineFile) {
    try {
      $baseline = Get-Content -Path $baselineFile -Raw | ConvertFrom-Json
      if ($null -ne $baseline) {
        $modeProp = $baseline.PSObject.Properties["table_profile"]
        $seatProp = $baseline.PSObject.Properties["table_position"]
        $legacyModeProp = $baseline.PSObject.Properties["profile"]
        $legacySeatProp = $baseline.PSObject.Properties["position"]

        if ($null -eq $modeProp -and $null -ne $legacyModeProp) {
          $modeProp = $legacyModeProp
        }
        if ($null -eq $seatProp -and $null -ne $legacySeatProp) {
          $seatProp = $legacySeatProp
        }

        if ($null -ne $modeProp -and $null -ne $seatProp) {
          $selectedMode = [string]$modeProp.Value
          $selectedSeat = [string]$seatProp.Value

          $validModes = @("tight", "normal", "aggressive")
          $validPositions = @("utg", "mp", "co", "btn", "sb", "bb")

          if (($validModes -contains $selectedMode) -and ($validPositions -contains $selectedSeat)) {
            $result.table_profile = $selectedMode
            $result.table_position = $selectedSeat
            $result.profile_source = $baselineFile
            $result.position_source = $baselineFile
            $result.source = "baseline_best.json"
            return $result
          }
        }
      }
    }
    catch {
      if (-not $Quiet) {
        Write-Warning "Falha ao ler baseline_best.json: $baselineFile"
      }
    }
  }

  $bestMode = Get-TitanBestSweepItem -Directory $Directory -Mode "profile" -Dimension "profile" -Quiet:$Quiet
  $bestSeat = Get-TitanBestSweepItem -Directory $Directory -Mode "position" -Dimension "position" -Quiet:$Quiet

  if ($null -ne $bestMode -and -not [string]::IsNullOrWhiteSpace($bestMode.item)) {
    $result.table_profile = $bestMode.item
    $result.profile_source = $bestMode.file
    $result.source = "sweep_summary"
  }

  if ($null -ne $bestSeat -and -not [string]::IsNullOrWhiteSpace($bestSeat.item)) {
    $result.table_position = $bestSeat.item
    $result.position_source = $bestSeat.file
    $result.source = "sweep_summary"
  }

  return $result
}

function Write-TitanBaselineFile {
  param(
    [string]$Directory,
    [string]$TableMode,
    [string]$TableSeat,
    [string]$ModeSource,
    [string]$SeatSource,
    [bool]$AutoSelected,
    [switch]$Quiet
  )

  if ([string]::IsNullOrWhiteSpace($Directory) -or [string]::IsNullOrWhiteSpace($TableMode) -or [string]::IsNullOrWhiteSpace($TableSeat)) {
    return $null
  }

  try {
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $filePath = Join-Path $Directory "baseline_best.json"

    $payload = [PSCustomObject]@{
      generated_at    = (Get-Date).ToString("o")
      table_profile   = $TableMode
      table_position  = $TableSeat
      auto_selected   = $AutoSelected
      profile_source  = $ModeSource
      position_source = $SeatSource
    }

    $json = $payload | ConvertTo-Json -Depth 5
    Set-Content -Path $filePath -Value $json -Encoding UTF8
    return $filePath
  }
  catch {
    if (-not $Quiet) {
      Write-Warning "Falha ao salvar baseline_best.json: $($_.Exception.Message)"
    }
    return $null
  }
}

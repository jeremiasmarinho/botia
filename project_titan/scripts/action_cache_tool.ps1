param(
  [ValidateSet("list", "prune", "delete", "clear")]
  [string]$Mode = "list",
  [string]$CacheFile = "",
  [string]$Scope = "",
  [string]$TableId = "",
  [string]$Session = "",
  [ValidateRange(1, 500)]
  [int]$MaxScopes = 50,
  [switch]$Json
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($CacheFile)) {
  $envCacheFile = $env:TITAN_ACTION_CALIBRATION_FILE
  if ([string]::IsNullOrWhiteSpace($envCacheFile)) {
    $CacheFile = "reports/action_calibration_cache.json"
  }
  else {
    $CacheFile = $envCacheFile
  }
}

if ([System.IO.Path]::IsPathRooted($CacheFile)) {
  $resolvedCacheFile = $CacheFile
}
else {
  $resolvedCacheFile = Join-Path $projectRoot $CacheFile
}

if (-not [string]::IsNullOrWhiteSpace($env:TITAN_ACTION_CALIBRATION_MAX_SCOPES)) {
  $envMaxScopes = 0
  if ([int]::TryParse($env:TITAN_ACTION_CALIBRATION_MAX_SCOPES, [ref]$envMaxScopes)) {
    if ($envMaxScopes -ge 1 -and $envMaxScopes -le 500 -and -not $PSBoundParameters.ContainsKey("MaxScopes")) {
      $MaxScopes = $envMaxScopes
    }
  }
}

function Get-EmptyPayload {
  return [ordered]@{
    version    = 1
    updated_at = (Get-Date).ToString("s")
    scopes     = [ordered]@{}
  }
}

function Read-CachePayload {
  param([string]$Path)

  if (-not (Test-Path $Path)) {
    return Get-EmptyPayload
  }

  try {
    $raw = Get-Content -Path $Path -Raw | ConvertFrom-Json -AsHashtable
    if ($null -eq $raw -or -not ($raw -is [hashtable])) {
      return Get-EmptyPayload
    }

    if (-not $raw.ContainsKey("scopes") -or -not ($raw.scopes -is [hashtable])) {
      $raw.scopes = [ordered]@{}
    }

    if (-not $raw.ContainsKey("version")) {
      $raw.version = 1
    }

    return $raw
  }
  catch {
    Write-Warning "Falha ao ler cache '$Path'. Usando payload vazio."
    return Get-EmptyPayload
  }
}

function Get-SortedScopeKeys {
  param([hashtable]$Scopes)

  $rows = @()
  foreach ($scopeKey in $Scopes.Keys) {
    $scopeEntry = $Scopes[$scopeKey]
    if (-not ($scopeEntry -is [hashtable])) {
      continue
    }

    $updatedAtRaw = ""
    if ($scopeEntry.ContainsKey("updated_at") -and $scopeEntry.updated_at -is [string]) {
      $updatedAtRaw = [string]$scopeEntry.updated_at
    }

    $updatedAtParsed = Get-Date "1970-01-01"
    if (-not [string]::IsNullOrWhiteSpace($updatedAtRaw)) {
      try {
        $updatedAtParsed = [DateTime]::Parse($updatedAtRaw)
      }
      catch {
        $updatedAtParsed = Get-Date "1970-01-01"
      }
    }

    $rows += [PSCustomObject]@{
      key        = [string]$scopeKey
      updated_at = $updatedAtParsed
    }
  }

  return $rows | Sort-Object updated_at -Descending | Select-Object -ExpandProperty key
}

function Write-CachePayload {
  param(
    [string]$Path,
    [hashtable]$Payload
  )

  $Payload.updated_at = (Get-Date).ToString("s")

  $targetDir = Split-Path -Parent $Path
  if (-not [string]::IsNullOrWhiteSpace($targetDir)) {
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
  }

  $tempPath = "$Path.tmp"
  $json = $Payload | ConvertTo-Json -Depth 8
  Set-Content -Path $tempPath -Value $json -Encoding UTF8
  Move-Item -Path $tempPath -Destination $Path -Force
}

$payload = Read-CachePayload -Path $resolvedCacheFile
$normalizedScopes = [ordered]@{}
foreach ($scopeKey in $payload.scopes.Keys) {
  if (-not ($payload.scopes[$scopeKey] -is [hashtable])) {
    continue
  }

  $scopeValue = $payload.scopes[$scopeKey]
  $points = [ordered]@{}
  $updatedAt = ""

  if ($scopeValue.ContainsKey("updated_at") -and $scopeValue.updated_at -is [string]) {
    $updatedAt = [string]$scopeValue.updated_at
  }

  $rawPoints = $null
  if ($scopeValue.ContainsKey("points") -and $scopeValue.points -is [hashtable]) {
    $rawPoints = $scopeValue.points
  }
  else {
    # backward compatibility: scope was directly a points dictionary
    $rawPoints = $scopeValue
  }

  foreach ($actionName in @("fold", "call", "raise", "raise_2x", "raise_2_5x", "raise_pot", "raise_confirm")) {
    if (-not $rawPoints.ContainsKey($actionName)) {
      continue
    }

    $rawPoint = $rawPoints[$actionName]
    if ($rawPoint -is [array] -and $rawPoint.Count -eq 2) {
      $x = 0
      $y = 0
      if ([int]::TryParse([string]$rawPoint[0], [ref]$x) -and [int]::TryParse([string]$rawPoint[1], [ref]$y)) {
        $points[$actionName] = @($x, $y)
      }
      continue
    }

    if ($rawPoint -is [hashtable] -and $rawPoint.ContainsKey("x") -and $rawPoint.ContainsKey("y")) {
      $x = 0
      $y = 0
      if ([int]::TryParse([string]$rawPoint.x, [ref]$x) -and [int]::TryParse([string]$rawPoint.y, [ref]$y)) {
        $points[$actionName] = @($x, $y)
      }
    }
  }

  $normalizedScopes[$scopeKey] = [ordered]@{
    updated_at = if ([string]::IsNullOrWhiteSpace($updatedAt)) { "" } else { $updatedAt }
    points     = $points
  }
}
$payload.scopes = $normalizedScopes

if ($Mode -eq "list") {
  $items = @()
  foreach ($scopeKey in Get-SortedScopeKeys -Scopes $payload.scopes) {
    $entry = $payload.scopes[$scopeKey]
    $parts = $scopeKey -split "::", 2
    $tablePart = if ($parts.Count -ge 1) { $parts[0] } else { "" }
    $sessionPart = if ($parts.Count -ge 2) { $parts[1] } else { "" }
    $items += [PSCustomObject]@{
      scope         = $scopeKey
      table_id      = $tablePart
      session       = $sessionPart
      updated_at    = [string]$entry.updated_at
      actions       = @($entry.points.Keys)
      actions_count = @($entry.points.Keys).Count
    }
  }

  if ($Json) {
    [PSCustomObject]@{
      cache_file   = $resolvedCacheFile
      total_scopes = $items.Count
      scopes       = $items
    } | ConvertTo-Json -Depth 6
  }
  else {
    Write-Host "[ACTION-CACHE] file=$resolvedCacheFile total_scopes=$($items.Count)"
    foreach ($item in $items) {
      Write-Host "[ACTION-CACHE] scope=$($item.scope) updated_at=$($item.updated_at) actions=$($item.actions -join ',')"
    }
  }
  exit 0
}

if ($Mode -eq "prune") {
  $orderedKeys = @(Get-SortedScopeKeys -Scopes $payload.scopes)
  $toRemove = @()
  if ($orderedKeys.Count -gt $MaxScopes) {
    $toRemove = $orderedKeys[$MaxScopes..($orderedKeys.Count - 1)]
  }

  foreach ($scopeKey in $toRemove) {
    $payload.scopes.Remove($scopeKey)
  }

  Write-CachePayload -Path $resolvedCacheFile -Payload $payload

  Write-Host "[ACTION-CACHE] pruned=$($toRemove.Count) kept=$([Math]::Min($orderedKeys.Count, $MaxScopes)) file=$resolvedCacheFile"
  exit 0
}

if ($Mode -eq "delete") {
  $targetScope = $Scope
  if ([string]::IsNullOrWhiteSpace($targetScope)) {
    if (-not [string]::IsNullOrWhiteSpace($TableId) -or -not [string]::IsNullOrWhiteSpace($Session)) {
      $effectiveTable = if ([string]::IsNullOrWhiteSpace($TableId)) { "table_default" } else { $TableId }
      $effectiveSession = if ([string]::IsNullOrWhiteSpace($Session)) { "default" } else { $Session }
      $targetScope = "$effectiveTable::$effectiveSession"
    }
  }

  if ([string]::IsNullOrWhiteSpace($targetScope)) {
    throw "Para modo delete, informe -Scope ou (-TableId e opcionalmente -Session)."
  }

  if (-not $payload.scopes.ContainsKey($targetScope)) {
    Write-Host "[ACTION-CACHE] scope_not_found=$targetScope file=$resolvedCacheFile"
    exit 0
  }

  $payload.scopes.Remove($targetScope)
  Write-CachePayload -Path $resolvedCacheFile -Payload $payload
  Write-Host "[ACTION-CACHE] deleted_scope=$targetScope file=$resolvedCacheFile"
  exit 0
}

if ($Mode -eq "clear") {
  $removed = $payload.scopes.Count
  $payload.scopes = [ordered]@{}
  Write-CachePayload -Path $resolvedCacheFile -Payload $payload
  Write-Host "[ACTION-CACHE] cleared_scopes=$removed file=$resolvedCacheFile"
  exit 0
}

throw "Modo inválido: $Mode"
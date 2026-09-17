#requires -Version 7.2
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('ProvisionStarted','ProvisionSucceeded','RunnerStarted','RunnerSucceeded','RunnerFailed','CostCheckpoint','CleanupStarted','RunnerDeallocated','GroupsDeleted')]
    [string]$Event,
    [ValidateSet('Bootstrap','SyncSource','Configure','Qualify','DeployCode','Train','Admission','ResetLive','Live','Disable','CheckNetwork')][string]$Phase,
    [ValidateSet('preflight','prepare','build','promote')][string]$TrainingStage,
    [ValidatePattern('^[a-z][a-z0-9-]{0,31}$')][string]$Variant = 'baseline',
    [ValidatePattern('^[a-z0-9][a-z0-9-]{0,63}$')][string[]]$Cases = @(),
    [ValidateSet('duplicate','conflict','admission-rejection','human-corrected-release','denied-review','concurrent-review')][string[]]$Protocols = @(),
    [ValidatePattern('^[A-Za-z0-9._-]{1,64}$')][string]$DeploymentName = 'fax-private-test',
    [AllowNull()][Nullable[decimal]]$SpendToDateUsd = $null
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($null -ne $SpendToDateUsd -and ($SpendToDateUsd -lt 0 -or $SpendToDateUsd -gt 1000000)) { throw 'Invalid reported spend.' }
$directory = Join-Path (Split-Path -Parent $PSScriptRoot) 'artifacts'
$null = New-Item -ItemType Directory -Path $directory -Force
$entry = [ordered]@{
    recordedAtUtc = [DateTimeOffset]::UtcNow.ToString('o')
    event = $Event
    phase = $Phase
    trainingStage = $TrainingStage
    variant = $Variant
    selectedCases = $Cases
    selectedProtocols = $Protocols
    deploymentName = $DeploymentName
    subscriptionId = '79398e05-f318-4010-a508-d913c3763e39'
    tenantId = '16b3c013-d300-468d-ac64-7eda0820b6d3'
    location = 'westus2'
    resourceGroups = @('rg-fax-e2e-westus2','rg-fax-authoring-e2e-westus2')
    cumulativeSpendReportedUsd = $SpendToDateUsd
    stopAndAskUsd = 100
    maximumTestWindowHours = 72
}
$entry | ConvertTo-Json -Compress | Add-Content (Join-Path $directory 'test-run-ledger.jsonl') -Encoding utf8NoBOM

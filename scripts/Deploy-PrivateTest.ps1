#requires -Version 7.2
[CmdletBinding()]
param(
    [ValidateSet('Build', 'Preflight', 'Validate', 'WhatIf', 'Provision', 'Outputs')]
    [string]$Mode = 'Build',
    [string]$ParameterFile = (Join-Path $PSScriptRoot '..\infra\private-test.example.bicepparam'),
    [string]$DeploymentName = 'fax-private-test',
    [ValidateRange(1, 72)][int]$PlannedHours = 72,
    [ValidateRange(0, 100)][decimal]$SpendToDateUsd = 0,
    [string]$Ep1QuotaEvidencePath,
    [switch]$DeploymentApproved
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Mode -eq 'Provision' -and -not $DeploymentApproved) { throw 'No provisioning approval. Architecture/implementation approval is insufficient.' }
$subscription = '79398e05-f318-4010-a508-d913c3763e39'
$tenant = '16b3c013-d300-468d-ac64-7eda0820b6d3'
$root = Split-Path -Parent $PSScriptRoot
$artifacts = Join-Path $root 'artifacts'
$null = New-Item -ItemType Directory -Path $artifacts -Force
$template = Join-Path $artifacts 'private-test.json'
$parameters = Join-Path $artifacts 'private-test.parameters.json'
$az = (Get-Command az -ErrorAction Stop).Source
function Invoke-AzJson([string[]]$Arguments) {
    $text = & $az @Arguments --subscription $subscription --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw "Azure operation failed: $($Arguments[0..1] -join ' ')" }
    if ($text) { return ($text | ConvertFrom-Json -AsHashtable) }
}
if ($Mode -ne 'Build') {
    $context = Invoke-AzJson @('account', 'show')
    if ($context.id -ne $subscription -or $context.tenantId -ne $tenant) { throw 'Wrong Azure tenant/subscription; sign in with the approved context.' }
}
if ($Mode -eq 'Preflight') {
    $providers = @('Microsoft.Network','Microsoft.Compute','Microsoft.ManagedIdentity','Microsoft.Storage','Microsoft.Web','Microsoft.CognitiveServices','Microsoft.Insights','Microsoft.OperationalInsights')
    foreach ($provider in $providers) {
        $state = Invoke-AzJson @('provider','show','--namespace',$provider,'--query','registrationState')
        if ($state -ne 'Registered') { throw "$provider is not registered. Registration is a separately approved write; stopping." }
    }
    $skus = @(Invoke-AzJson @('vm','list-skus','--location','westus2','--size','Standard_D2s_v7','--all'))
    $sku = @($skus | Where-Object { $_.name -eq 'Standard_D2s_v7' -and $_.resourceType -eq 'virtualMachines' })
    if ($sku.Count -ne 1 -or @($sku[0].restrictions).Count -gt 0) { throw 'D2s_v7 unavailable or restricted; do not substitute a VM without rechecking.' }
    $usage = Invoke-AzJson @('vm','list-usage','--location','westus2')
    $family = @($usage | Where-Object { $_.name.value -eq $sku[0].family })
    if ($family.Count -ne 1 -or ($family[0].limit - $family[0].currentValue) -lt 2) { throw 'Insufficient or unknown D2s_v7 family vCPU quota.' }
    $total = @($usage | Where-Object { $_.name.value -eq 'cores' })
    if ($total.Count -ne 1 -or ($total[0].limit - $total[0].currentValue) -lt 2) { throw 'Insufficient or unknown regional vCPU quota.' }
    $image = Invoke-AzJson @('vm','image','show','--location','westus2','--urn','Debian:debian-12:12-gen2:latest')
    if ($image.architecture -ne 'x64' -or $image.hyperVGeneration -ne 'V2' -or $image.osDiskImage.operatingSystem -ne 'Linux' -or ($image.ContainsKey('plan') -and $null -ne $image.plan)) {
        throw 'Runner image must remain available Linux x64 Gen2 without unapproved Marketplace plan terms.'
    }
    # App Service quota is independent from Microsoft.Compute family quota.
    $web = Invoke-AzJson @('rest','--method','get','--url',"https://management.azure.com/subscriptions/$subscription/providers/Microsoft.Web/locations/westus2/usages?api-version=2024-11-01")
    $ep1 = @($web.value | Where-Object { $_.name.value -eq 'EP1' })
    if ($ep1.Count -ne 1) {
        # Some subscriptions expose only Dedicated-plan quotas in this API.
        if (-not $Ep1QuotaEvidencePath -or $Ep1QuotaEvidencePath -like '*.example.json') {
            throw 'Regional usages API did not return EP1. Supply a fresh read-only EP1 quota evidence JSON via -Ep1QuotaEvidencePath; do not infer it from Premium v3/Compute quota. EP1_AZ is not used.'
        }
        $evidence = Get-Content $Ep1QuotaEvidencePath -Raw | ConvertFrom-Json -AsHashtable
        $checkedAt = [DateTimeOffset]::Parse($evidence.checkedAtUtc)
        if ($evidence.subscriptionId -ne $subscription -or $evidence.tenantId -ne $tenant -or $evidence.location -ne 'westus2' -or $evidence.sku -ne 'EP1' -or
            $checkedAt -lt [DateTimeOffset]::UtcNow.AddHours(-24) -or $checkedAt -gt [DateTimeOffset]::UtcNow.AddMinutes(5) -or -not $evidence.source) {
            throw 'EP1 evidence must be attributable, correct-scope, non-zonal and less than 24 hours old.'
        }
        $ep1 = @($evidence)
    }
    if (($ep1[0].limit - $ep1[0].currentValue) -lt 2) { throw 'Require two available EP1 workers for the bounded max-two test and deployment headroom.' }
    $base = [math]::Round($PlannedHours * (0.1999 + 0.132 + 0.045 + 11 * 0.01), 2)
    $ceiling = [math]::Round($PlannedHours * (2 * 0.1999 + 0.132 + 0.045 + 11 * 0.01), 2)
    if ($SpendToDateUsd + $ceiling -ge 100) { throw 'USD100 test stop-and-ask limit would be reached at the permitted two-instance ceiling before usage.' }
    Write-Output "Read-only preflight passed. Approximate $PlannedHours-hour one-instance base USD$base; two-instance base USD$ceiling before disks, public IP, usage, tax. Monitor actual cost; USD100 is not an automatic Azure spending cap."
    return
}
if ($Mode -eq 'Outputs') {
    $outputs = Invoke-AzJson @('deployment','sub','show','--name',$DeploymentName,'--query','properties.outputs')
    $outputs | ConvertTo-Json -Depth 20 | Set-Content (Join-Path $artifacts 'private-test.outputs.json') -Encoding utf8NoBOM
    Write-Output 'Safe deployment IDs/endpoints saved to artifacts/private-test.outputs.json.'
    return
}
& $az bicep build --file (Join-Path $root 'infra\main.bicep') --outfile (Join-Path $artifacts 'main.json') --only-show-errors
if ($LASTEXITCODE -ne 0) { throw 'Original entrypoint failed to compile.' }
& $az bicep build --file (Join-Path $root 'infra\private-test.bicep') --outfile $template --only-show-errors
if ($LASTEXITCODE -ne 0) { throw 'Private test wrapper failed to compile.' }
& $az bicep build-params --file $ParameterFile --outfile $parameters --only-show-errors
if ($LASTEXITCODE -ne 0) { throw 'Parameters failed to compile.' }
if ($Mode -eq 'Build') { Write-Output 'Both entrypoints compiled. No Azure resources queried or changed.'; return }
$raw = Get-Content $parameters -Raw
if ($ParameterFile -like '*.example.bicepparam' -or $raw -match 'REPLACE_|00000000-0000-0000-0000-000000000000') {
    throw 'Online validation requires completed non-example parameters.'
}
$p = ($raw | ConvertFrom-Json -AsHashtable).parameters
if ($p.namePrefix.value -notmatch '^[a-z][a-z0-9-]{1,10}[a-z0-9]$') { throw 'Invalid test namePrefix.' }
if ($p.apiClientId.value -notmatch '^[0-9a-fA-F-]{36}$') { throw 'Invalid API client ID.' }
if ($p.runnerSshPublicKey.value -notmatch '^ssh-(ed25519|rsa) [A-Za-z0-9+/]+=*( .*)?$') { throw 'Supply a valid SSH public key, never a private key.' }
if ($p.alertEmail.value -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { throw 'Supply a monitored alert email address.' }
$humanClients = @()
$humanPrincipals = @()
if ($p.ContainsKey('humanReviewClientApplicationIds')) { $humanClients = @($p.humanReviewClientApplicationIds.value) }
if ($p.ContainsKey('humanReviewPrincipalObjectIds')) { $humanPrincipals = @($p.humanReviewPrincipalObjectIds.value) }
if (($humanClients.Count -gt 0) -ne ($humanPrincipals.Count -gt 0)) { throw 'Human review requires both approved delegated client IDs and reviewer object IDs.' }
foreach ($identity in @($humanClients) + @($humanPrincipals)) {
    $guid = [guid]::Empty
    if (-not [guid]::TryParse($identity, [ref]$guid) -or $guid -eq [guid]::Empty) { throw 'Human review authorization requires real nonempty GUIDs.' }
}
foreach ($pair in @{ runtimeResourceGroupName='rg-fax-e2e-westus2'; authoringResourceGroupName='rg-fax-authoring-e2e-westus2'; location='westus2' }.GetEnumerator()) {
    if ($p.ContainsKey($pair.Key) -and $p[$pair.Key].value -ne $pair.Value) { throw "This scoped test script forbids overriding $($pair.Key)." }
}
$common = @('--location','westus2','--name',$DeploymentName,'--template-file',$template,'--parameters',"@$parameters")
if ($Mode -eq 'WhatIf') {
    & $az deployment sub what-if @common --subscription $subscription --result-format ResourceIdOnly --only-show-errors
    if ($LASTEXITCODE -ne 0) { throw 'What-if failed.' }
} elseif ($Mode -eq 'Validate') {
    $null = Invoke-AzJson (@('deployment','sub','validate') + $common)
    Write-Output 'ARM validation passed; no deployment performed.'
} elseif ($Mode -eq 'Provision') {
    if (-not $DeploymentApproved) { throw 'No provisioning approval. Architecture/implementation approval is insufficient.' }
    & $PSCommandPath -Mode Preflight -PlannedHours $PlannedHours -SpendToDateUsd $SpendToDateUsd -Ep1QuotaEvidencePath $Ep1QuotaEvidencePath
    $null = Invoke-AzJson (@('deployment','sub','validate') + $common)
    & (Join-Path $PSScriptRoot 'Write-TestLedger.ps1') -Event ProvisionStarted -DeploymentName $DeploymentName -SpendToDateUsd $SpendToDateUsd
    $null = Invoke-AzJson (@('deployment','sub','create') + $common)
    & (Join-Path $PSScriptRoot 'Write-TestLedger.ps1') -Event ProvisionSucceeded -DeploymentName $DeploymentName -SpendToDateUsd $SpendToDateUsd
    & $PSCommandPath -Mode Outputs -DeploymentName $DeploymentName
    Write-Output 'Infrastructure created. Admission remains disabled; no live qualification claim.'
}

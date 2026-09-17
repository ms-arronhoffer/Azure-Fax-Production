#requires -Version 7.2
[CmdletBinding()]
param(
    [ValidateSet('Build', 'Validate', 'WhatIf', 'Infrastructure', 'Code')]
    [string]$Mode = 'Build',
    [string]$ParameterFile = (Join-Path $PSScriptRoot '..\infra\main.example.bicepparam'),
    [string]$SubscriptionId,
    [string]$ResourceGroup,
    [string]$DeploymentName = 'private-fax',
    [string]$FunctionAppName,
    [string]$PackagePath = (Join-Path $PSScriptRoot '..\artifacts\fax-function.zip'),
    [ValidateRange(60, 3600)][int]$DeploymentTimeoutSeconds = 900,
    [switch]$DeploymentApproved
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$VerbosePreference = 'SilentlyContinue'
$DebugPreference = 'SilentlyContinue'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ($Mode -in @('Infrastructure', 'Code') -and -not $DeploymentApproved) {
    throw 'Cloud writes require separate deployment approval and -DeploymentApproved. Architecture approval is not deployment approval.'
}
$az = (Get-Command az -ErrorAction Stop).Source

function Invoke-AzQuiet {
    param([string[]]$Arguments)
    & $az @Arguments --only-show-errors --output none
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI operation failed: $($Arguments[0..1] -join ' ')" }
}

function Test-GuidValue {
    param([string]$Value)
    $parsed = [Guid]::Empty
    return [Guid]::TryParse($Value, [ref]$parsed) -and $parsed -ne [Guid]::Empty
}

if ($Mode -ne 'Build') {
    if (-not (Test-GuidValue $SubscriptionId) -or [string]::IsNullOrWhiteSpace($ResourceGroup)) {
        throw 'Non-Build modes require explicit SubscriptionId and ResourceGroup; no subscription discovery is performed.'
    }
}

if ($Mode -ne 'Code') {
    $paramsPath = (Resolve-Path -LiteralPath $ParameterFile).Path
    $artifacts = Join-Path $root 'artifacts'
    $null = New-Item -ItemType Directory -Path $artifacts -Force
    $templateJson = Join-Path $artifacts 'main.json'
    $parameterJson = Join-Path $artifacts 'main.parameters.json'
    Invoke-AzQuiet @('bicep', 'build', '--file', (Join-Path $root 'infra\main.bicep'), '--outfile', $templateJson)
    Invoke-AzQuiet @('bicep', 'build-params', '--file', $paramsPath, '--outfile', $parameterJson)
    if ($Mode -eq 'Build') {
        Write-Output 'Bicep and parameters compiled locally. No Azure resources were queried or changed.'
        return
    }
    $compiled = Get-Content -LiteralPath $parameterJson -Raw | ConvertFrom-Json -AsHashtable
    $values = $compiled.parameters
    if ($paramsPath -like '*.example.bicepparam' -or (Get-Content $parameterJson -Raw).Contains('00000000-0000-0000-0000-000000000000')) {
        throw 'Replace example parameters with real approved resource IDs before an online operation.'
    }
    if ($values.namePrefix.value -notmatch '^[a-z][a-z0-9-]{1,10}[a-z0-9]$') {
        throw 'namePrefix must contain 3-12 lowercase letters/digits/internal hyphens and start with a letter.'
    }
    if (-not (Test-GuidValue $values.apiClientId.value)) { throw 'apiClientId must be a nonempty GUID.' }
    foreach ($id in $values.allowedClientApplicationIds.value) {
        if (-not (Test-GuidValue $id)) { throw 'Calling application IDs must be nonempty GUIDs.' }
    }
    if ($values.ContainsKey('allowedPrincipalObjectIds')) {
        foreach ($id in $values.allowedPrincipalObjectIds.value) {
            if (-not (Test-GuidValue $id)) { throw 'Caller principal object IDs must be nonempty GUIDs.' }
        }
    }
    if ($values.integrationSubnetId.value -eq $values.privateEndpointSubnetId.value) {
        throw 'Integration and private endpoint subnets must differ.'
    }
    $minimum = if ($values.ContainsKey('minimumInstanceCount')) { $values.minimumInstanceCount.value } else { 1 }
    $maximum = if ($values.ContainsKey('maximumInstanceCount')) { $values.maximumInstanceCount.value } else { 5 }
    if ($minimum -gt $maximum) { throw 'minimumInstanceCount cannot exceed maximumInstanceCount.' }
    if ($values.ContainsKey('processingEnabled') -and $values.processingEnabled.value) {
        throw 'Infrastructure deployments require processingEnabled=false. Enable admission separately after model and live acceptance gates.'
    }
    $common = @('--subscription', $SubscriptionId, '--resource-group', $ResourceGroup, '--name', $DeploymentName,
        '--template-file', $templateJson, '--parameters', "@$parameterJson")
    switch ($Mode) {
        'Validate' { Invoke-AzQuiet (@('deployment', 'group', 'validate') + $common) }
        'WhatIf' {
            # Resource IDs and change types only; do not display app-setting property values.
            & $az deployment group what-if @common --result-format ResourceIdOnly --only-show-errors
            if ($LASTEXITCODE -ne 0) { throw 'ARM what-if failed.' }
        }
        'Infrastructure' {
            Invoke-AzQuiet (@('deployment', 'group', 'validate') + $common)
            Invoke-AzQuiet (@('deployment', 'group', 'create') + $common + @('--mode', 'Incremental'))
            Write-Output 'Infrastructure deployment completed. Runtime admission is disabled; RBAC propagation and live validation are still required.'
        }
    }
    return
}

if ($FunctionAppName -notmatch '^[a-z][a-z0-9-]{1,58}[a-z0-9]$') {
    throw 'Code mode requires an explicit valid FunctionAppName from the deployment output.'
}
$package = (Resolve-Path -LiteralPath $PackagePath).Path
if (-not (Test-Path -LiteralPath "$package.sha256" -PathType Leaf)) { throw 'Missing package SHA256 sidecar; run Package.ps1 first.' }
$expectedHash = (Get-Content -LiteralPath "$package.sha256" -Raw).Trim()
if ((Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash -ne $expectedHash) { throw 'Package SHA256 mismatch.' }
$zip = [IO.Compression.ZipFile]::OpenRead($package)
try {
    $names = @($zip.Entries.FullName)
    if ('host.json' -notin $names -or 'pipeline/config.json' -notin $names -or -not ($names | Where-Object { $_ -like '.python_packages/lib/site-packages/azure/functions/*' })) {
        throw 'Not a runnable Linux Functions package. Run Package.ps1 on a Linux Python 3.11 builder.'
    }
    if ($names | Where-Object { $_ -match '(^|/)(local\.settings\.json|\.env|\.git)(/|$)' }) {
        throw 'Archive contains prohibited local configuration.'
    }
} finally { $zip.Dispose() }

$scm = [Uri]"https://$FunctionAppName.scm.azurewebsites.net"
$addresses = [Net.Dns]::GetHostAddresses($scm.Host)
$privateAddresses = @($addresses | Where-Object {
    $b = $_.GetAddressBytes()
    $b.Length -eq 4 -and ($b[0] -eq 10 -or ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or ($b[0] -eq 192 -and $b[1] -eq 168))
})
if ($privateAddresses.Count -ne $addresses.Count -or $privateAddresses.Count -eq 0) {
    throw 'SCM must resolve exclusively to private RFC1918 addresses. Run from the approved private runner; never enable public SCM.'
}

# Entra ARM token stays only in memory, never a command-line argument, file, or output.
$token = & $az account get-access-token --subscription $SubscriptionId --resource 'https://management.azure.com/' --query accessToken --output tsv --only-show-errors
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) { throw 'Unable to obtain deployment identity token.' }
$handler = [Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
$client = [Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds($DeploymentTimeoutSeconds)
$stream = $null
$content = $null
try {
    $client.DefaultRequestHeaders.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $token.Trim())
    # Fail closed on redeploy: disable admission before replacing code. CLI output is suppressed.
    Invoke-AzQuiet @('functionapp', 'config', 'appsettings', 'set', '--subscription', $SubscriptionId,
        '--resource-group', $ResourceGroup, '--name', $FunctionAppName, '--settings', 'PROCESSING_ENABLED=false')
    $stream = [IO.File]::OpenRead($package)
    $content = [Net.Http.StreamContent]::new($stream)
    $content.Headers.ContentType = [Net.Http.Headers.MediaTypeHeaderValue]::new('application/zip')
    $response = $client.PostAsync([Uri]::new($scm, '/api/zipdeploy?isAsync=true'), $content).GetAwaiter().GetResult()
    try {
        if (-not $response.IsSuccessStatusCode) { throw "SCM upload failed (HTTP $([int]$response.StatusCode)); response body intentionally not logged." }
        $poll = $response.Headers.Location
        if ($null -eq $poll) { throw 'SCM did not provide an asynchronous deployment status URL.' }
        if (-not $poll.IsAbsoluteUri) { $poll = [Uri]::new($scm, $poll) }
        if ($poll.Scheme -ne 'https' -or $poll.Host -ne $scm.Host -or $poll.Port -ne 443) {
            throw 'Refusing to send the token to a different status endpoint.'
        }
    } finally { $response.Dispose() }
    $deadline = [DateTime]::UtcNow.AddSeconds($DeploymentTimeoutSeconds)
    do {
        Start-Sleep -Seconds 5
        $result = $client.GetAsync($poll).GetAwaiter().GetResult()
        try {
            if (-not $result.IsSuccessStatusCode) { throw "SCM status check failed (HTTP $([int]$result.StatusCode))." }
            $status = $result.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
            if ($status.status -eq 3) { throw 'SCM deployment failed. Review deployment logs privately; no response bodies are printed.' }
            if ($status.status -eq 4) {
                Write-Output 'Code deployment completed; PROCESSING_ENABLED remains false. Verify host startup, identity, private networking, model configuration and alerts before enabling admission.'
                return
            }
        } finally { $result.Dispose() }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'SCM deployment status timed out. Do not retry blindly; inspect the private deployment status first.'
} catch {
    # HttpClient errors can contain sensitive HTTP details; emit only an operator-safe message.
    if ($_.Exception -is [Net.Http.HttpRequestException]) { throw 'Private SCM HTTP request failed. Check private DNS, routes, TLS and deployer RBAC.' }
    throw
} finally {
    $token = $null
    if ($null -ne $content) { $content.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
    $client.Dispose()
    $handler.Dispose()
}

#requires -Version 7.2
[CmdletBinding()]
param(
    [ValidateSet('Create', 'Assign')][string]$Mode = 'Create',
    [string]$DisplayName = 'fax-e2e',
    [string]$IdentityFile = (Join-Path $PSScriptRoot '..\artifacts\test-api-identity.json'),
    [string]$OutputsFile = (Join-Path $PSScriptRoot '..\artifacts\private-test.outputs.json'),
    [switch]$DeploymentApproved
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $DeploymentApproved) { throw 'Entra writes are not approved. Supply -DeploymentApproved only after explicit deployment/directory approval.' }
$subscription = '79398e05-f318-4010-a508-d913c3763e39'
$tenant = '16b3c013-d300-468d-ac64-7eda0820b6d3'
$context = az account show --subscription $subscription --output json --only-show-errors | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $context.tenantId -ne $tenant) { throw 'Wrong Azure context.' }
$token = az account get-access-token --subscription $subscription --resource-type ms-graph --query accessToken -o tsv --only-show-errors
if ($LASTEXITCODE -ne 0 -or -not $token) { throw 'Graph authentication failed; do not bypass directory authorization.' }
$headers = @{ Authorization = "Bearer $token" }
function Invoke-Graph([string]$Method, [string]$Path, [object]$Body) {
    $args = @{ Method=$Method; Uri="https://graph.microsoft.com/v1.0/$Path"; Headers=$headers; MaximumRedirection=0 }
    if ($null -ne $Body) { $args.Body = $Body | ConvertTo-Json -Depth 15 -Compress; $args.ContentType='application/json' }
    try { Invoke-RestMethod @args } catch { throw 'Graph operation failed. Check directory privileges/consent and partial-state IDs. Never enable anonymous access or silently retry creation.' }
}
try {
    if ($Mode -eq 'Create') {
        if (Test-Path $IdentityFile) { throw 'Identity record exists. Reuse it or investigate partial creation; do not create duplicates.' }
        foreach ($name in @("$DisplayName-api", "$DisplayName-client")) {
            $filter = [Uri]::EscapeDataString("displayName eq '$($name.Replace("'", "''"))'")
            $existing = Invoke-Graph GET "applications?`$filter=$filter&`$select=id" $null
            if (@($existing.value).Count) { throw 'An exact-name test application already exists. Investigate/reuse it; do not create duplicates.' }
        }
        $roles = @(
            @{ id=[guid]::NewGuid().ToString(); value='Fax.Submit'; displayName='Submit synthetic faxes'; description='Submit and inspect owned synthetic jobs'; isEnabled=$true; allowedMemberTypes=@('Application') },
            @{ id=[guid]::NewGuid().ToString(); value='Fax.Review'; displayName='Review synthetic faxes'; description='Review and release synthetic review jobs'; isEnabled=$true; allowedMemberTypes=@('Application') }
        )
        $api = Invoke-Graph POST 'applications' @{ displayName="$DisplayName-api"; signInAudience='AzureADMyOrg'; api=@{requestedAccessTokenVersion=2}; appRoles=$roles }
        $record = @{ apiClientId=$api.appId; apiObjectId=$api.id; roles=$roles; tenantId=$tenant; status='partial' }
        $null = New-Item -ItemType Directory -Path (Split-Path $IdentityFile -Parent) -Force
        $record | ConvertTo-Json -Depth 15 | Set-Content $IdentityFile -Encoding utf8NoBOM
        $null = Invoke-Graph PATCH "applications/$($api.id)" @{ identifierUris=@("api://$($api.appId)") }
        $sp = Invoke-Graph POST 'servicePrincipals' @{ appId=$api.appId; appRoleAssignmentRequired=$true }
        $record.apiServicePrincipalId = $sp.id
        $record | ConvertTo-Json -Depth 15 | Set-Content $IdentityFile -Encoding utf8NoBOM
        # Isolated client registration has no secrets, credentials or permissions. The VM uses UAMIs.
        $client = Invoke-Graph POST 'applications' @{ displayName="$DisplayName-client"; signInAudience='AzureADMyOrg' }
        $record.clientObjectId=$client.id; $record.clientId=$client.appId
        $record.status='created'
        $record | ConvertTo-Json -Depth 15 | Set-Content $IdentityFile -Encoding utf8NoBOM
        Write-Output "Isolated API/client IDs saved. Set Bicep apiClientId to $($api.appId). No credentials created."
    } else {
        $record = Get-Content $IdentityFile -Raw | ConvertFrom-Json -AsHashtable
        if ($record.status -ne 'created' -or $record.tenantId -ne $tenant) { throw 'Incomplete/wrong-tenant identity record; investigate first.' }
        $outputs = Get-Content $OutputsFile -Raw | ConvertFrom-Json -AsHashtable
        if ($outputs.apiClientId.value -ne $record.apiClientId) { throw 'Deployment and API identity record disagree.' }
        foreach ($item in @(@{identity='uploaderIdentity';role='Fax.Submit'},@{identity='reviewerIdentity';role='Fax.Review'})) {
            $principal = $outputs[$item.identity].value.principalId
            $role = @($record.roles | Where-Object value -eq $item.role)[0].id
            $existing = Invoke-Graph GET "servicePrincipals/$principal/appRoleAssignments" $null
            if (-not @($existing.value | Where-Object { $_.resourceId -eq $record.apiServicePrincipalId -and $_.appRoleId -eq $role }).Count) {
                $null = Invoke-Graph POST "servicePrincipals/$principal/appRoleAssignments" @{ principalId=$principal; resourceId=$record.apiServicePrincipalId; appRoleId=$role }
            }
        }
        Write-Output 'Uploader and reviewer have distinct API roles. Obtain fresh MI tokens and test positive/negative authorization.'
    }
} finally { $token=$null; $headers.Clear() }

#requires -Version 7.2
[CmdletBinding()]
param(
    [Parameter(Mandatory)][uri]$BaseUrl,
    [Parameter(Mandatory)][string]$FaxPath,
    [Parameter(Mandatory)][string]$Source,
    [securestring]$AccessToken,
    [guid]$IdempotencyKey = [guid]::NewGuid(),
    [ValidateSet('Completed', 'PendingHumanReview')][string]$ExpectedStatus = 'Completed',
    [ValidateRange(1, 3600)][int]$TimeoutSeconds = 600
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $BaseUrl.IsAbsoluteUri -or $BaseUrl.Scheme -ne 'https' -or
    $BaseUrl.AbsolutePath -ne '/' -or $BaseUrl.Query -or $BaseUrl.Fragment -or $BaseUrl.UserInfo) {
    throw 'BaseUrl must be the HTTPS origin of the approved Fax API, without a path, query, credentials, or fragment.'
}
$file = Get-Item -LiteralPath $FaxPath
if ($file -isnot [IO.FileInfo] -or $file.Length -eq 0) { throw 'FaxPath must be a nonempty synthetic test file.' }
$contentType = switch ($file.Extension) {
    '.pdf' { 'application/pdf' }
    '.tif' { 'image/tiff' }
    '.tiff' { 'image/tiff' }
    default { throw 'FaxPath must have a .pdf, .tif, or .tiff extension.' }
}
if (-not $AccessToken) { $AccessToken = Read-Host 'Fax API access token (input hidden)' -AsSecureString }
if ($AccessToken.Length -eq 0) { throw 'A Fax API access token is required.' }

function Invoke-FaxRequest {
    param([string]$Method, [uri]$Uri, [int]$ExpectedCode, [hashtable]$Options = @{})

    $remaining = [int][Math]::Floor($TimeoutSeconds - $clock.Elapsed.TotalSeconds)
    if ($remaining -lt 1) { throw 'Timed out before the next Fax API request.' }
    try {
        $response = Invoke-WebRequest -Method $Method -Uri $Uri -Authentication Bearer -Token $AccessToken `
            -MaximumRedirection 0 -TimeoutSec ([Math]::Min(30, $remaining)) -SkipHttpErrorCheck @Options
    } catch {
        throw "Fax API $Method request did not return a usable response. Check private connectivity and that the API URL does not redirect."
    }
    if ($response.StatusCode -ne $ExpectedCode) {
        throw "Fax API $Method returned HTTP $($response.StatusCode); expected $ExpectedCode. Check the user guide for authentication, source authorization, and retry guidance."
    }
    try {
        $body = ConvertFrom-Json -InputObject $response.Content -AsHashtable
    } catch {
        throw "Fax API $Method returned invalid JSON."
    }
    if ($body -isnot [System.Collections.IDictionary]) { throw "Fax API $Method did not return a JSON object." }
    [pscustomobject]@{ Body = $body; Headers = $response.Headers }
}

$clock = [Diagnostics.Stopwatch]::StartNew()
Write-Host "Submitting one synthetic fax. Idempotency key: $IdempotencyKey"
$submission = Invoke-FaxRequest -Method Post -Uri ([uri]::new($BaseUrl, '/api/faxes')) -ExpectedCode 202 -Options @{
    InFile = $file.FullName
    ContentType = $contentType
    Headers = @{ 'x-fax-source' = $Source; 'idempotency-key' = $IdempotencyKey.ToString() }
}
$faxId = [guid]::Empty
if (-not [guid]::TryParseExact([string]$submission.Body['faxId'], 'D', [ref]$faxId) -or $faxId -eq [guid]::Empty) {
    throw 'Fax API returned HTTP 202 without a valid fax ID.'
}
$statusPath = "/api/faxes/$faxId"
if ($submission.Body['statusUrl'] -cne $statusPath -or [string](@($submission.Headers['Location'])[0]) -cne $statusPath) {
    throw 'Fax API returned an unexpected statusUrl or Location.'
}
$statusUri = [uri]::new($BaseUrl, $statusPath)
Write-Host "Fax $faxId accepted (HTTP 202)."

$headers = $submission.Headers
$lastStatus = $null
while ($true) {
    $retryAfter = 0
    $delay = if ([int]::TryParse([string](@($headers['Retry-After'])[0]), [ref]$retryAfter) -and $retryAfter -gt 0) { $retryAfter } else { 10 }
    if ($clock.Elapsed.TotalSeconds + $delay + 1 -gt $TimeoutSeconds) {
        throw "Fax $faxId did not reach $ExpectedStatus before the polling deadline. Check its status before submitting again."
    }
    Start-Sleep -Seconds $delay
    $result = Invoke-FaxRequest -Method Get -Uri $statusUri -ExpectedCode 200
    if ($result.Body['faxId'] -cne $faxId.ToString()) { throw 'Status response does not match the submitted fax ID.' }
    $status = [string]$result.Body['status']
    switch -CaseSensitive ($status) {
        { $_ -cin 'Completed', 'PendingHumanReview', 'Failed' } {
            if ($status -cne $ExpectedStatus) { throw "Fax $faxId ended in $status; expected $ExpectedStatus." }
            if ($status -ceq 'Completed' -and -not $result.Body['manifest']) { throw "Fax $faxId completed without a manifest pointer." }
            return [pscustomobject]@{ FaxId = $faxId; Status = $status; IdempotencyKey = $IdempotencyKey }
        }
        { $_ -cin 'Accepted', 'Processing', 'Publishing' } {
            if ($status -cne $lastStatus) { Write-Host "Fax ${faxId}: $status" }
            $lastStatus = $status
            $headers = $result.Headers
        }
        default { throw "Fax $faxId returned an unrecognized status." }
    }
}
#requires -Version 7.2
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Bootstrap','SyncSource','Configure','Qualify','DeployCode','Train','Admission','ResetLive','Live','Disable','CheckNetwork')][string]$Phase,
    [string]$OutputsFile = (Join-Path $PSScriptRoot '..\artifacts\private-test.outputs.json'),
    [string]$SourceArchive = (Join-Path $PSScriptRoot '..\artifacts\runner-source.zip'),
    [string]$ConfigurationBundle,
    [ValidateSet('preflight','prepare','build','promote')][string]$TrainingStage,
    [ValidatePattern('^[a-z][a-z0-9-]{0,31}$')][string]$Variant = 'baseline',
    [ValidatePattern('^[a-z0-9][a-z0-9-]{0,63}$')][string[]]$Cases = @(),
    [ValidateSet('duplicate','conflict','admission-rejection','human-corrected-release','denied-review','concurrent-review')][string[]]$Protocols = @(),
    [ValidateRange(0, 1000000)][decimal]$SpendToDateUsd = 0,
    [switch]$DeploymentApproved
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$VerbosePreference = 'SilentlyContinue'
$DebugPreference = 'SilentlyContinue'
if (-not $DeploymentApproved) { throw 'RunCommand changes the VM and can invoke billable operations. Separate deployment approval is required.' }
if ($Phase -ne 'Disable' -and $SpendToDateUsd -ge 100) { throw 'USD100 cumulative test threshold reached. Stop and obtain further approval; Disable remains available.' }
if ($Phase -eq 'Train' -and -not $TrainingStage) { throw 'Train requires an explicit TrainingStage; inspect generated OCR/labels before build.' }
if ($Phase -eq 'Live' -and $Cases.Count -eq 0) { throw 'Live requires explicit compatible Cases; one configuration cannot satisfy all conflicting route/HL7 fixtures.' }
& (Join-Path $PSScriptRoot 'Clear-CompletedTestCommands.ps1') -DeploymentApproved
$subscription = '79398e05-f318-4010-a508-d913c3763e39'
$tenant = '16b3c013-d300-468d-ac64-7eda0820b6d3'
$context = az account show --subscription $subscription -o json --only-show-errors | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $context.tenantId -ne $tenant) { throw 'Wrong Azure context.' }
$outputs = Get-Content $OutputsFile -Raw | ConvertFrom-Json -AsHashtable
$vmId = $outputs.runnerResourceId.value
if ($vmId -notmatch "^/subscriptions/$subscription/resourceGroups/rg-fax-e2e-westus2/providers/Microsoft.Compute/virtualMachines/[a-z0-9-]+$") { throw 'Runner ID outside approved scope.' }
$protected = @()
if ($Phase -in @('Bootstrap','SyncSource')) {
    $bytes = [IO.File]::ReadAllBytes((Resolve-Path $SourceArchive).Path)
    if ($bytes.Length -gt 524288) { throw 'Archive exceeds 512 KiB protected bootstrap limit.' }
    $encoded = [Convert]::ToBase64String($bytes)
    $chunkCount = [int][Math]::Ceiling($encoded.Length / 49152.0)
    $protected = @(
        @{name='SOURCE_CHUNK_COUNT';value=[string]$chunkCount},
        @{name='SOURCE_SHA256';value=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()},
        @{name='RUNNER_CONTEXT';value=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($outputs | ConvertTo-Json -Depth 20 -Compress)))}
    )
    for ($index = 0; $index -lt $chunkCount; $index++) {
        $offset = $index * 49152
        $protected += @{name=('SOURCE_ARCHIVE_{0:D3}' -f $index);value=$encoded.Substring($offset,[Math]::Min(49152,$encoded.Length-$offset))}
    }
    $scriptFile = if ($Phase -eq 'Bootstrap') { 'runner-bootstrap.sh' } else { 'runner-sync-source.sh' }
    $script = Get-Content (Join-Path $PSScriptRoot $scriptFile) -Raw
} elseif ($Phase -eq 'Configure') {
    if (-not $ConfigurationBundle) { throw 'Configure requires an explicit nonsecret JSON ConfigurationBundle.' }
    $configuration = [IO.File]::ReadAllBytes((Resolve-Path $ConfigurationBundle).Path)
    if ($configuration.Length -gt 65536) { throw 'Configuration bundle exceeds 64 KiB.' }
    $parsed = [Text.Encoding]::UTF8.GetString($configuration) | ConvertFrom-Json -AsHashtable
    foreach ($key in $parsed.Keys) {
        if ($key -notin @('training-config','runtime-config','live-config')) { throw 'Unknown configuration bundle member.' }
    }
    $protected = @(
        @{name='CONFIGURATION_BUNDLE';value=[Convert]::ToBase64String($configuration)},
        @{name='CONFIGURATION_VARIANT';value=$Variant}
    )
    $script = @'
#!/bin/bash
set -euo pipefail
set +x
umask 077
cd /opt/fax-test/source/Azure-Fax-Production
if flock -n /opt/fax-test/runner.lock .venv/bin/python - <<'PY' > /opt/fax-test/logs/configure.log 2>&1
import base64, hashlib, json, os, re
from pathlib import Path
bundle = json.loads(base64.b64decode(os.environ.pop("CONFIGURATION_BUNDLE"), validate=True))
assert bundle and set(bundle) <= {"training-config", "runtime-config", "live-config"}
variant = os.environ.pop("CONFIGURATION_VARIANT")
assert re.fullmatch(r"[a-z][a-z0-9-]{0,31}", variant)
directory = Path("artifacts") if variant == "baseline" else Path("artifacts/variants", variant)
directory.mkdir(parents=True, exist_ok=True)
for name, data in bundle.items():
    assert isinstance(data, dict)
    target = Path("artifacts") if name == "training-config" else directory
    (target / (name + ".json")).write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
runtime = directory / "runtime-config.json"
live = directory / "live-config.json"
if runtime.exists() and live.exists():
    data = json.loads(live.read_text())
    data["runtimeConfigSha256"] = hashlib.sha256(json.dumps(json.loads(runtime.read_text()), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    live.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
PY
then
  echo 'Nonsecret runtime/test configuration staged privately.'
else
  echo 'Configuration staging failed; inspect private runner log.'
  exit 1
fi
'@
} else {
    $arguments = if ($Phase -eq 'Train') { " --training-stage '$TrainingStage'" } else { '' }
    $arguments += " --variant '$Variant'"
    foreach ($case in $Cases) { $arguments += " --case '$case'" }
    foreach ($protocol in $Protocols) { $arguments += " --protocol '$protocol'" }
    $lock = if ($Phase -eq 'Disable') { '' } else { 'flock -n /opt/fax-test/runner.lock ' }
    $script = @"
#!/bin/bash
set -euo pipefail
set +x
umask 077
cd /opt/fax-test/source/Azure-Fax-Production
export TMPDIR=/opt/fax-test/scratch TMP=/opt/fax-test/scratch TEMP=/opt/fax-test/scratch
if ${lock}.venv/bin/python scripts/private_runner.py --phase '$Phase'$arguments > /opt/fax-test/logs/$Phase.log 2>&1; then
  echo 'Private runner phase succeeded; see private artifacts for qualification evidence.'
else
  echo 'Private runner phase failed; inspect private logs. No sensitive response is returned.'
  exit 1
fi
"@
}
$payload = @{
    location='westus2'
    properties=@{
        source=@{script=$script.Replace("`r`n","`n")}
        protectedParameters=$protected
        asyncExecution=$false
        timeoutInSeconds=3600
        treatFailureAsDeploymentFailure=$true
    }
} | ConvertTo-Json -Depth 12 -Compress
$token = az account get-access-token --subscription $subscription --resource 'https://management.azure.com/' --query accessToken -o tsv --only-show-errors
if ($LASTEXITCODE -ne 0 -or -not $token) { throw 'ARM token acquisition failed.' }
$commandName = "fax-$($Phase.ToLowerInvariant())-$([guid]::NewGuid().ToString('N').Substring(0,8))"
$uri = "https://management.azure.com$vmId/runCommands/${commandName}?api-version=2024-11-01"
$headers = @{Authorization="Bearer $token"}
$ledgerArguments = @{Phase=$Phase;SpendToDateUsd=$SpendToDateUsd;Variant=$Variant;Cases=$Cases;Protocols=$Protocols}
if ($TrainingStage) { $ledgerArguments.TrainingStage=$TrainingStage }
try {
    & (Join-Path $PSScriptRoot 'Write-TestLedger.ps1') -Event RunnerStarted @ledgerArguments
    # Keep token and source archive out of CLI arguments, disk payloads and command output.
    $null = Invoke-RestMethod -Uri $uri -Method Put -Headers $headers -Body $payload -ContentType 'application/json' -MaximumRedirection 0
    $deadline = [DateTime]::UtcNow.AddMinutes(65)
    do {
        Start-Sleep -Seconds 15
        $result = Invoke-RestMethod -Uri "$uri&`$expand=instanceView" -Headers $headers -MaximumRedirection 0
        $state = $result.properties.provisioningState
        if ($state -eq 'Failed') { throw 'RunCommand failed; inspect its private VM log.' }
        $view = $result.properties.PSObject.Properties['instanceView']
        if ($view -and $view.Value.executionState -eq 'Succeeded') {
            if ($view.Value.exitCode -ne 0) { throw 'Runner command returned nonzero exit code.' }
            & (Join-Path $PSScriptRoot 'Write-TestLedger.ps1') -Event RunnerSucceeded @ledgerArguments
            Write-Output "Runner phase $Phase completed ($commandName). No command output/source/token was printed."
            return
        }
        if ($view -and $view.Value.executionState -in @('Failed','TimedOut','Canceled')) { throw 'Runner execution did not succeed.' }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'RunCommand timed out; inspect status before retrying.'
} catch {
    & (Join-Path $PSScriptRoot 'Write-TestLedger.ps1') -Event RunnerFailed @ledgerArguments
    throw 'Private RunCommand did not complete successfully. Inspect scoped ARM execution state and private VM logs; do not print response bodies.'
} finally { $token=$null; $payload=$null; $bytes=$null; $protected=$null; $headers.Clear() }

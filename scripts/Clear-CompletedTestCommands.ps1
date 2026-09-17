#requires -Version 7.2
[CmdletBinding()]
param([switch]$DeploymentApproved)
$ErrorActionPreference='Stop'
if (-not $DeploymentApproved) { throw 'Approved test deployment required for temporary command cleanup.' }
$scope=@('--resource-group','rg-fax-e2e-westus2','--vm-name','vm-faxe2e-runner','--subscription','79398e05-f318-4010-a508-d913c3763e39')
$names=az vm run-command list @scope --query '[].name' -o json --only-show-errors | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot list temporary test commands.' }
if (@($names).Count -lt 20) { Write-Host 'Managed command capacity available.'; return }
$removed=0
foreach($name in $names) {
    if ($name -notmatch '^fax-(bootstrap|checknetwork|configure|syncsource|train|qualify|deploycode|live|disable|evidence)(-[0-9a-f]{8})?$') { continue }
    $view=az vm run-command show @scope --name $name --expand instanceView --query 'instanceView.{state:executionState,exitCode:exitCode,start:startTime,end:endTime}' -o json --only-show-errors | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot verify temporary command completion.' }
    if ($view.state -notin @('Succeeded','Failed','TimedOut','Canceled')) { continue }
    $record=@{name=$name;state=$view.state;exitCode=$view.exitCode;start=$view.start;end=$view.end}
    $record | ConvertTo-Json -Compress | Add-Content (Join-Path $PSScriptRoot '..\artifacts\command-history.jsonl') -Encoding utf8NoBOM
    az vm run-command delete @scope --name $name --yes --no-wait --only-show-errors
    if ($LASTEXITCODE -ne 0) { throw 'Temporary command cleanup failed; saved execution metadata is retained.' }
    $removed++
    if ($removed -ge 5) { break }
}
Write-Host 'Completed temporary command resources released; VM artifacts and model/test evidence retained.'

#requires -Version 7.2
[CmdletBinding()]
param(
    [string]$OutputsFile = (Join-Path $PSScriptRoot '..\artifacts\private-test.outputs.json'),
    [ValidatePattern('^[a-z0-9-]{1,24}$')][string]$Version = 'synthetic-v1',
    [ValidateRange(5,15)][int]$TrainingCountPerType = 5,
    [string]$OutputFile = (Join-Path $PSScriptRoot '..\artifacts\training-bundle.json')
)
$ErrorActionPreference = 'Stop'
$outputs = Get-Content -LiteralPath $OutputsFile -Raw | ConvertFrom-Json -AsHashtable
$authorModels = @{}
$runtimeModels = @{}
foreach ($kind in @('page', 'type', 'exam', 'order', 'rx')) {
    $authorModels[$kind] = "$Version-author-$kind"
    $runtimeModels[$kind] = "$Version-runtime-$kind"
}
$bundle = @{
    'training-config' = @{
        runnerClientId = $outputs.authorIdentity.value.clientId
        trainingContainerUrl = $outputs.trainingStorageAccountUrl.value.TrimEnd('/') + '/training'
        trainingPrefix = "$Version/"
        buildMode = 'template'
        maxTrainingHours = 0.5
        corpusVersion = $Version
        trainingCountPerType = $TrainingCountPerType
        authoring = @{
            endpoint = $outputs.authoringDocumentIntelligenceEndpoint.value.TrimEnd('/')
            resourceId = $outputs.authoringDocumentIntelligenceResourceId.value
            models = $authorModels
        }
        runtime = @{
            endpoint = $outputs.runtimeDocumentIntelligenceEndpoint.value.TrimEnd('/')
            resourceId = $outputs.runtimeDocumentIntelligenceResourceId.value
            models = $runtimeModels
        }
    }
}
if (Test-Path -LiteralPath $OutputFile) {
    throw 'Training bundle already exists. Reuse the existing version/state; do not silently reset paid model operations.'
}
$bundle | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutputFile -Encoding utf8NoBOM
Write-Host 'Nonsecret training bundle generated from actual deployment outputs; no model training or runtime activation performed.'

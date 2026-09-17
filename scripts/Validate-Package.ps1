[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $PythonExecutable,

    [switch] $SkipBicep
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

# Run only the package's own tests. Use an explicitly chosen interpreter so a
# developer's unrelated global Python installation is not selected by accident.
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw 'PythonExecutable must be the full path to the prepared Python interpreter.'
}
$tests = Get-ChildItem -LiteralPath (Join-Path $root 'app\tests') -Filter 'test*.py' -File -Recurse
if ($tests.Count -eq 0) {
    throw 'No package tests found; refusing an empty validation run.'
}
Push-Location (Join-Path $root 'app')
try {
    & $PythonExecutable -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw 'Python unit tests failed.'
    }
}
finally {
    Pop-Location
}

if ($SkipBicep) {
    Write-Warning 'Bicep compilation explicitly skipped; infrastructure is not validated by this run.'
}
else {
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        throw 'Azure CLI is required for Bicep compilation. Install it using the deployment guide.'
    }
    $compiledPath = Join-Path ([System.IO.Path]::GetTempPath()) ("fax-bicep-{0}.json" -f [guid]::NewGuid())
    try {
        & az bicep build --file (Join-Path $root 'infra\main.bicep') --outfile $compiledPath
        if ($LASTEXITCODE -ne 0) {
            throw 'Bicep compilation failed.'
        }
        $null = Get-Content -LiteralPath $compiledPath -Raw | ConvertFrom-Json
    }
    finally {
        if (Test-Path -LiteralPath $compiledPath) {
            Remove-Item -LiteralPath $compiledPath
        }
    }
}

# Reject malformed or disconnected draw.io references without submitting the
# private architecture to an external diagram service.
$diagrams = @(
    Get-ChildItem -LiteralPath (Join-Path $root 'architecture') -File |
        Where-Object { $_.Name -like '*.drawio' -or $_.Name -like '*.drawio.xml' }
)
if ($diagrams.Count -eq 0) {
    throw 'No editable draw.io architecture found.'
}
foreach ($diagram in $diagrams) {
    [xml] $xml = Get-Content -LiteralPath $diagram.FullName -Raw
    $models = @($xml.SelectNodes('//mxGraphModel'))
    if ($models.Count -eq 0) {
        throw "Expected uncompressed editable graph: $($diagram.Name)"
    }
    foreach ($model in $models) {
        $ids = @{}
        foreach ($cell in $model.root.mxCell) {
            if ($ids.ContainsKey($cell.id)) {
                throw "Duplicate diagram ID: $($cell.id)"
            }
            $ids[$cell.id] = $true
        }
        foreach ($cell in $model.root.mxCell) {
            if ($cell.edge -eq '1' -and
                (-not $ids.ContainsKey($cell.source) -or -not $ids.ContainsKey($cell.target))) {
                throw "Broken diagram edge: $($cell.id)"
            }
        }
    }
}

Write-Host 'Local package checks passed. No live cloud, model quality, private connectivity, or load tests were performed.'

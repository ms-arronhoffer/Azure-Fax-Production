$ErrorActionPreference = 'Stop'
$manifest = Get-Content -Raw (Join-Path $PSScriptRoot 'icons\manifest.json') | ConvertFrom-Json
$approved = @($manifest.icons | ForEach-Object { $_.sha256 })
$imageCount = 0
function Assert-Icon([string] $base64) {
    $bytes = [Convert]::FromBase64String($base64)
    if ([Convert]::ToBase64String($bytes) -cne $base64) { throw 'Noncanonical base64 image' }
    [xml]$svg = [Text.Encoding]::UTF8.GetString($bytes)
    if ($svg.DocumentElement.LocalName -ne 'svg') { throw 'Embedded image is not SVG' }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
    if ($hash -notin $approved) { throw "Embedded image differs from official source: $hash" }
    $script:imageCount++
}
foreach ($icon in $manifest.icons) {
    $hash = (Get-FileHash -Algorithm SHA256 (Join-Path $PSScriptRoot ('icons\' + $icon.file))).Hash.ToLowerInvariant()
    if ($hash -ne $icon.sha256) { throw "Icon source changed: $($icon.file)" }
}
[xml]$doc = Get-Content -Raw (Join-Path $PSScriptRoot 'Azure-Fax-Production.drawio')
if (@($doc.mxfile.diagram).Count -ne 4) { throw 'Expected four editable diagram pages' }
if (@($doc.mxfile.diagram | Where-Object id -eq 'reporting').Count -ne 1) { throw 'Missing reporting metrics page' }
$totalCells = 0
$totalEdges = 0
foreach ($page in $doc.mxfile.diagram) {
    $cells = @($page.mxGraphModel.root.mxCell)
    $ids = @($cells | ForEach-Object { $_.id })
    if (@($ids | Select-Object -Unique).Count -ne $ids.Count) { throw 'Duplicate draw.io cell ID' }
    foreach ($cell in $cells) {
        foreach ($attribute in @('parent', 'source', 'target')) {
            if ($cell.HasAttribute($attribute) -and $cell.GetAttribute($attribute) -notin $ids) {
                throw "Broken $attribute on $($cell.id)"
            }
        }
        if ($cell.edge -eq '1') {
            if (-not $cell.HasAttribute('source') -or -not $cell.HasAttribute('target')) { throw 'Unconnected edge' }
            $totalEdges++
        }
        if ($cell.style -match 'image=') {
            if ($cell.style -notmatch 'image=data:image/svg\+xml,([A-Za-z0-9+/=]+);') { throw 'External or invalid draw.io image' }
            Assert-Icon $Matches[1]
        }
    }
    $totalCells += $cells.Count
}
foreach ($name in @('topology.svg', 'workflow.svg', 'criteria.svg', 'reporting.svg')) {
    [xml]$svg = Get-Content -Raw (Join-Path $PSScriptRoot $name)
    if ($svg.DocumentElement.LocalName -ne 'svg') { throw "$name is not SVG" }
    $ids = @($svg.SelectNodes('//*[@id]') | ForEach-Object { $_.id })
    if (@($ids | Select-Object -Unique).Count -ne $ids.Count) { throw "Duplicate SVG ID in $name" }
    foreach ($image in $svg.SelectNodes('//*[local-name()="image"]')) {
        if ($image.href -notmatch '^data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)$') { throw "External image in $name" }
        Assert-Icon $Matches[1]
        if ($image.preserveAspectRatio -ne 'xMidYMid meet') { throw 'Icon aspect ratio not preserved' }
    }
    if ($svg.SelectNodes('//*[local-name()="script" or local-name()="foreignObject"]').Count) { throw 'Unexpected executable/HTML content in SVG' }
}
$gallery = Get-Content -Raw (Join-Path $PSScriptRoot 'preview.html')
if ($gallery -match '<script|(?:src|href)=["'']https?://') { throw 'Gallery has remote or executable dependencies' }
foreach ($match in [regex]::Matches($gallery, 'href="data:image/svg\+xml;base64,([^"]+)"')) {
    Assert-Icon $match.Groups[1].Value
}
foreach ($file in @('Azure-Fax-Production.drawio', 'topology.svg', 'workflow.svg', 'criteria.svg', 'reporting.svg', 'README.md', 'ICON-SOURCES.md')) {
    if (-not (Test-Path (Join-Path $PSScriptRoot $file))) { throw "Missing deliverable: $file" }
}
$result = [ordered]@{
    status = 'PASS'
    editablePages = 4
    cells = $totalCells
    connectedEdges = $totalEdges
    approvedOfficialIcons = $approved.Count
    embeddedIconsValidated = $imageCount
    validation = 'XML parse, unique IDs, parent/source/target references, base64 roundtrip, SHA-256 byte equality, standalone SVG aspect ratio, offline gallery'
}
$workflow = $doc.mxfile.diagram | Where-Object id -eq 'workflow'
$edges = @($workflow.mxGraphModel.root.mxCell | Where-Object edge -eq '1')
$expected = @{
    'extract' = @('classify-type')
    'candidate' = @('gate', 'human-approved')
    'hl7-required' = @('candidate')
    'transform' = @('hl7-required')
    'validate-hl7' = @('transform')
    'hl7-skipped' = @('hl7-required')
    'primary-json' = @('hl7-skipped', 'validate-hl7')
    'manifest' = @('routed-blobs')
    'consumer' = @('manifest')
}
foreach ($target in $expected.Keys) {
    $actual = @($edges | Where-Object target -eq $target | ForEach-Object source | Sort-Object)
    if (($actual -join ',') -ne (($expected[$target] | Sort-Object) -join ',')) {
        throw "Release barrier mismatch for $target"
    }
}
$original = Join-Path $PSScriptRoot '..\..\Azure-Fax-Architecture\azure-fax-processing.drawio.xml'
if (Test-Path -LiteralPath $original) {
    if ((Get-FileHash $original).Hash -ne (Get-FileHash (Join-Path $PSScriptRoot 'Azure-Fax-Production.drawio')).Hash) {
        throw 'Original and production drawings are out of sync'
    }
}
$result | ConvertTo-Json | Write-Output
Write-Output 'Release graph barriers and available original-drawing mirror verified.'

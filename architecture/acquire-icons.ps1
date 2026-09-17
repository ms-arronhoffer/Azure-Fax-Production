$ErrorActionPreference = 'Stop'
$source = 'https://arch-center.azureedge.net/icons/Azure_Public_Service_Icons_V24.zip'
$archive = Join-Path $PSScriptRoot 'Azure_Public_Service_Icons_V24.zip'
$destination = Join-Path $PSScriptRoot 'icons'
$selection = [ordered]@{
    functions = 'compute/10029-icon-service-Function-Apps.svg'
    storage = 'storage/10086-icon-service-Storage-Accounts.svg'
    blob = 'general/10839-icon-service-Storage-Container.svg'
    queue = 'general/10840-icon-service-Storage-Queue.svg'
    documentIntelligence = 'ai + machine learning/00819-icon-service-Form-Recognizers.svg'
    privateEndpoint = 'other/02579-icon-service-Private-Endpoints.svg'
    virtualNetwork = 'networking/10061-icon-service-Virtual-Networks.svg'
    dns = 'networking/10064-icon-service-DNS-Zones.svg'
    identity = 'identity/10227-icon-service-Managed-Identities.svg'
    insights = 'monitor/00012-icon-service-Application-Insights.svg'
    logs = 'monitor/00009-icon-service-Log-Analytics-Workspaces.svg'
    monitor = 'monitor/00001-icon-service-Monitor.svg'
}
New-Item -ItemType Directory -Path $destination -Force | Out-Null
if (-not (Test-Path $archive)) {
    Invoke-WebRequest -Uri $source -OutFile $archive
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
$manifest = @()
try {
    foreach ($item in $selection.GetEnumerator()) {
        $entryPath = 'Azure_Public_Service_Icons/Icons/' + $item.Value
        $entry = $zip.GetEntry($entryPath)
        if ($null -eq $entry) { throw "Missing official icon: $entryPath" }
        $file = Join-Path $destination ($item.Key + '.svg')
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $file, $true)
        $manifest += [ordered]@{
            key = $item.Key
            file = $item.Key + '.svg'
            archiveEntry = $entryPath
            sha256 = (Get-FileHash -Algorithm SHA256 -Path $file).Hash.ToLowerInvariant()
        }
    }
} finally {
    $zip.Dispose()
}
[ordered]@{
    source = $source
    terms = 'https://learn.microsoft.com/en-us/azure/architecture/icons/'
    archiveSha256 = (Get-FileHash -Algorithm SHA256 -Path $archive).Hash.ToLowerInvariant()
    icons = $manifest
} | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $destination 'manifest.json') -Encoding utf8
Remove-Item -LiteralPath $archive
Write-Output "Extracted $($manifest.Count) unmodified official SVG icons; archive removed."

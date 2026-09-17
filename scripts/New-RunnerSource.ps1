#requires -Version 7.2
[CmdletBinding()]
param([string]$OutputPath = (Join-Path $PSScriptRoot '..\artifacts\runner-source.zip'))
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$output = [IO.Path]::GetFullPath($OutputPath)
if (-not $output.StartsWith("$root$([IO.Path]::DirectorySeparatorChar)", [StringComparison]::OrdinalIgnoreCase)) { throw 'Output must remain inside the project.' }
$null = New-Item -ItemType Directory -Path (Split-Path $output -Parent) -Force
$staged = Join-Path (Split-Path $output -Parent) ("runner-source-" + [guid]::NewGuid().ToString('N') + '.zip')
$archive = [IO.Compression.ZipFile]::Open($staged, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($folder in @('app','e2e','scripts')) {
        foreach ($file in Get-ChildItem (Join-Path $root $folder) -Recurse -File -Force) {
            $relative = [IO.Path]::GetRelativePath($root, $file.FullName).Replace('\','/')
            if ($relative -match '(^|/)(\.venv[^/]*|venv|artifacts|generated|evidence|__pycache__|\.python_packages|\.pytest_cache|\.git)(/|$)') { continue }
            if ($file.Extension -notin @('.py','.json','.txt','.sh','.ps1') -or $file.Name -match '^(local\.settings|\.env)' -or $file.Name -like '*.local.*') { continue }
            if ($file.LinkTarget) { throw 'Source symlinks are forbidden.' }
            if ($file.Length -gt 1048576) { throw "Source file exceeds 1 MiB: $relative" }
            $text = [IO.File]::ReadAllText($file.FullName)
            if ($text -match '-----BEGIN (RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----') { throw "Private key content forbidden: $relative" }
            $null = [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive,$file.FullName,"Azure-Fax-Production/$relative",[IO.Compression.CompressionLevel]::Optimal)
        }
    }
} catch {
    $archive.Dispose()
    Remove-Item $staged -Force
    throw
} finally { $archive.Dispose() }
if ((Get-Item $staged).Length -gt 524288) { Remove-Item $staged; throw 'Source archive exceeds the 512 KiB protected bootstrap limit.' }
Move-Item $staged $output -Force
Write-Output "Bounded source archive: $output"
Write-Output "SHA256: $((Get-FileHash $output -Algorithm SHA256).Hash)"
Write-Output 'No PEM files or dependencies included. This is source, not a deployable Function package.'

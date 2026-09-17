#requires -Version 7.2
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$outputFolder = Join-Path $root 'artifacts'
$output = Join-Path $outputFolder 'Azure-Fax-Production-source.zip'
$null = New-Item -ItemType Directory -Path $outputFolder -Force
$temporaryZip = Join-Path $outputFolder ("source-{0}.zip" -f [guid]::NewGuid())
$sourceRoots = @('app', 'e2e', 'architecture', 'docs', 'infra', 'power-platform', 'scripts', '.azure')
$extensions = @('.py', '.json', '.bicep', '.bicepparam', '.ps1', '.sh', '.md', '.svg', '.drawio', '.xml', '.html', '.cjs', '.txt')

# This archive is source/documentation, NOT the Linux dependency-bearing ZIP
# accepted by Deploy.ps1. Whitelist source types and omit machine-local state.
$archive = [IO.Compression.ZipFile]::Open($temporaryZip, [IO.Compression.ZipArchiveMode]::Create)
try {
    $files = @(
        Get-Item -LiteralPath (Join-Path $root 'README.md'), (Join-Path $root '.gitignore')
        foreach ($folder in $sourceRoots) {
            $path = Join-Path $root $folder
            if (Test-Path -LiteralPath $path -PathType Container) {
                Get-ChildItem -LiteralPath $path -File -Recurse -Force
            }
        }
    )
    foreach ($file in ($files | Sort-Object FullName)) {
        $relative = [IO.Path]::GetRelativePath($root, $file.FullName)
        if ($relative -match '^e2e[\\/](generated|evidence)([\\/]|$)') { continue }
        if ($relative -match '(^|[\\/])(\.venv[^\\/]*|venv|__pycache__|\.python_packages|artifacts|\.git|\.copilot-azure)([\\/]|$)') { continue }
        if ($file.Name -eq 'local.settings.json' -or $file.Name -like '.env*' -or $file.Name -like '*.local.*') { continue }
        if ($file.Name -in @('main.json', 'main.parameters.json')) { continue }
        if ($file.Extension -notin $extensions -and $file.Name -notin @('.gitignore', '.funcignore')) { continue }
        if ($file.LinkTarget) {
            throw "Refusing a source symlink: $relative"
        }
        $entryName = 'Azure-Fax-Production/' + $relative.Replace('\', '/')
        $null = [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive, $file.FullName, $entryName, [IO.Compression.CompressionLevel]::Optimal)
    }
}
catch {
    $archive.Dispose()
    if (Test-Path -LiteralPath $temporaryZip) { Remove-Item -LiteralPath $temporaryZip }
    throw
}
finally {
    $archive.Dispose()
}

Move-Item -LiteralPath $temporaryZip -Destination $output -Force
$hash = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash
Write-Host "Source archive: $output"
Write-Host "SHA256: $hash"
Write-Host 'Source package only. Build the runnable Linux artifact with Package.ps1 after configuring real models.'

#requires -Version 7.2
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ConfigPath,
    [string]$PythonExecutable = 'python3.11',
    [string]$OutputPath = (Join-Path $PSScriptRoot '..\artifacts\fax-function.zip')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$app = Join-Path $root 'app'
$config = (Resolve-Path -LiteralPath $ConfigPath).Path
$output = [IO.Path]::GetFullPath($OutputPath)
if (-not $IsLinux) {
    throw 'Build on a Linux x64 Python 3.11 runner with PowerShell 7. Windows-built binary dependencies are not deployable to this Linux Function App.'
}
if (-not $output.StartsWith("$root$([IO.Path]::DirectorySeparatorChar)", [StringComparison]::Ordinal)) {
    throw 'OutputPath must be inside this package directory.'
}
foreach ($required in @('host.json', 'requirements.txt', 'function_app.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $app $required) -PathType Leaf)) {
        throw "Missing app entry file: $required"
    }
}
# This is a JSON syntax gate, not model/schema or extraction-quality validation.
$null = Get-Content -LiteralPath $config -Raw | ConvertFrom-Json -AsHashtable
$python = (Get-Command $PythonExecutable -CommandType Application -ErrorAction Stop).Source
& $python -c "import platform,sys; assert sys.version_info[:2] == (3,11), 'Python 3.11 required'; assert platform.machine().lower() in ('x86_64','amd64'), 'Linux x64 required'"
if ($LASTEXITCODE -ne 0) { throw 'Python platform validation failed.' }

$buildRoot = Join-Path $root 'artifacts'
$stage = Join-Path $buildRoot ("stage-" + [Guid]::NewGuid().ToString('N'))
$scratch = Join-Path $buildRoot ("scratch-" + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $stage, $scratch -Force
$oldTemp = @{}
foreach ($name in @('TMPDIR', 'TEMP', 'TMP')) {
    $oldTemp[$name] = [Environment]::GetEnvironmentVariable($name)
    [Environment]::SetEnvironmentVariable($name, $scratch)
}
try {
    $files = Get-ChildItem -LiteralPath $app -Recurse -File -Force
    foreach ($file in $files) {
        $relative = [IO.Path]::GetRelativePath($app, $file.FullName)
        $segments = $relative -split '[\\/]'
        if (@($segments | Where-Object { $_ -in @('.venv', 'venv', '.python_packages', '__pycache__', 'tests', 'scripts', '.pytest_cache', '.git') }).Count) { continue }
        if ($file.Name -like 'local.settings*') { continue }
        $include = $file.Extension -eq '.py' -or $relative -in @('host.json', 'requirements.txt')
        if (-not $include) { continue }
        if ($null -ne $file.LinkTarget) { throw "Symlinks are not packaged: $relative" }
        $destination = Join-Path $stage $relative
        $null = New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force
        Copy-Item -LiteralPath $file.FullName -Destination $destination
    }
    $null = New-Item -ItemType Directory -Path (Join-Path $stage 'pipeline') -Force
    Copy-Item -LiteralPath $config -Destination (Join-Path $stage 'pipeline\config.json')
    $dependencies = Join-Path $stage '.python_packages\lib\site-packages'
    $null = New-Item -ItemType Directory -Path $dependencies -Force
    & $python -m pip install --disable-pip-version-check --no-cache-dir --quiet --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 --python-version 3.11 --implementation cp --only-binary=:all: --requirement (Join-Path $app 'requirements.txt') --target $dependencies
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. No deployment package was produced.' }
    if (-not (Test-Path -LiteralPath (Join-Path $dependencies 'azure\functions'))) {
        throw 'azure-functions is missing from packaged dependencies.'
    }
    $null = New-Item -ItemType Directory -Path (Split-Path $output -Parent) -Force
    $stagedZip = Join-Path $buildRoot ("package-" + [Guid]::NewGuid().ToString('N') + '.zip')
    try {
        [IO.Compression.ZipFile]::CreateFromDirectory($stage, $stagedZip, [IO.Compression.CompressionLevel]::Optimal, $false)
        $archive = [IO.Compression.ZipFile]::OpenRead($stagedZip)
        try {
            $entries = @($archive.Entries.FullName)
            foreach ($entry in @('host.json', 'function_app.py', 'requirements.txt', 'pipeline/config.json')) {
                if ($entry -notin $entries) { throw "Archive missing required entry: $entry" }
            }
            if (-not ($entries | Where-Object { $_ -like '.python_packages/lib/site-packages/azure/functions/*' })) {
                throw 'Archive omitted hidden dependency directory.'
            }
        } finally { $archive.Dispose() }
        Move-Item -LiteralPath $stagedZip -Destination $output -Force
    } finally {
        if (Test-Path -LiteralPath $stagedZip) { Remove-Item -LiteralPath $stagedZip -Force }
    }
    $hash = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$output.sha256" -Value $hash -Encoding utf8NoBOM
    Write-Output "Package: $output"
    Write-Output "SHA256: $hash"
    Write-Output 'Admission remains disabled. A package build is not model validation or production acceptance.'
} finally {
    foreach ($name in $oldTemp.Keys) { [Environment]::SetEnvironmentVariable($name, $oldTemp[$name]) }
    # These are only this invocation's unique, generated staging directories.
    Remove-Item -LiteralPath $stage, $scratch -Recurse -Force
}

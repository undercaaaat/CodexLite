$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$sourceRoot = Join-Path $projectRoot ".tools\codex-no-tools"
$sourceBinary = Join-Path $sourceRoot "codex-exec.exe"
$sourceBuildInfo = Join-Path $sourceRoot "build-info.json"
$sourceLicense = Join-Path $sourceRoot "source\LICENSE"
$sourceNotice = Join-Path $sourceRoot "source\NOTICE"
$buildRoot = Join-Path $projectRoot ".build"
$assetName = "CodexLite-0.5.0-codex-0.144.6-windows-x64"
$bundleRoot = Join-Path $buildRoot $assetName
$archivePath = Join-Path $buildRoot ($assetName + ".zip")
$wheelDirectory = Join-Path $buildRoot "wheels-windows"

foreach ($requiredPath in @(
    $sourceBinary,
    $sourceBuildInfo,
    $sourceLicense,
    $sourceNotice
)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "required build input is missing: $requiredPath"
    }
}

if (Test-Path -LiteralPath $bundleRoot) {
    throw "bundle directory already exists: $bundleRoot"
}
if (Test-Path -LiteralPath $archivePath) {
    throw "bundle archive already exists: $archivePath"
}

New-Item -ItemType Directory -Force -Path (
    Join-Path $bundleRoot "bin"
) | Out-Null
New-Item -ItemType Directory -Force -Path (
    Join-Path $bundleRoot "licenses"
) | Out-Null
New-Item -ItemType Directory -Force -Path (
    Join-Path $bundleRoot "packages"
) | Out-Null
New-Item -ItemType Directory -Force -Path $wheelDirectory | Out-Null

& python -m pip wheel $projectRoot --no-deps --wheel-dir $wheelDirectory
if ($LASTEXITCODE -ne 0) {
    throw "failed to build the CodexLite Python wheel"
}

$wheelPath = Join-Path $wheelDirectory "codex_batch-0.5.0-py3-none-any.whl"
if (-not (Test-Path -LiteralPath $wheelPath)) {
    throw "expected wheel was not produced: $wheelPath"
}

Copy-Item -LiteralPath $sourceBinary -Destination (
    Join-Path $bundleRoot "bin\codex-exec.exe"
)
Copy-Item -LiteralPath $wheelPath -Destination (
    Join-Path $bundleRoot "packages\codex_batch-0.5.0-py3-none-any.whl"
)
Copy-Item -LiteralPath (Join-Path $projectRoot "README-QUICKSTART.md") `
    -Destination (Join-Path $bundleRoot "README-QUICKSTART.md")
Copy-Item -LiteralPath (Join-Path $projectRoot "MODIFICATIONS.md") `
    -Destination (Join-Path $bundleRoot "MODIFICATIONS.md")
Copy-Item -LiteralPath (Join-Path $projectRoot "LICENSE") `
    -Destination (Join-Path $bundleRoot "LICENSE-CODEXLITE.txt")
Copy-Item -LiteralPath $sourceLicense -Destination (
    Join-Path $bundleRoot "licenses\LICENSE-CODEX-APACHE-2.0.txt"
)
Copy-Item -LiteralPath $sourceNotice -Destination (
    Join-Path $bundleRoot "licenses\NOTICE-CODEX.txt"
)
Copy-Item -LiteralPath (
    Join-Path $PSScriptRoot "codex-rust-v0.144.6-no-tools.patch"
) -Destination (Join-Path $bundleRoot "codex-rust-v0.144.6-no-tools.patch")

$localBuildInfo = Get-Content -LiteralPath $sourceBuildInfo -Encoding UTF8 |
    ConvertFrom-Json
$releaseBuildInfo = [ordered]@{
    architecture = "x86_64"
    cargo = $localBuildInfo.cargo
    codex_commit = $localBuildInfo.codex_commit
    codex_tag = $localBuildInfo.codex_tag
    mode = $localBuildInfo.mode
    platform = "windows"
    rust_target = "x86_64-pc-windows-msvc"
    rustc = $localBuildInfo.rustc
}
$buildInfoJson = $releaseBuildInfo | ConvertTo-Json
[System.IO.File]::WriteAllText(
    (Join-Path $bundleRoot "build-info.json"),
    $buildInfoJson + "`n",
    [System.Text.UTF8Encoding]::new($false)
)

Compress-Archive -LiteralPath $bundleRoot -DestinationPath $archivePath `
    -CompressionLevel Optimal

Get-Item -LiteralPath $archivePath | Select-Object FullName, Length

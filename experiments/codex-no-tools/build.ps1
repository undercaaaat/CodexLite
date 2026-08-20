param(
    [string]$Destination
)

$ErrorActionPreference = "Stop"
$tag = "rust-v0.144.6"
$commit = "5d1fbf26c43abc65a203928b2e31561cb039e06d"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$patchPath = Join-Path $PSScriptRoot "codex-rust-v0.144.6-no-tools.patch"
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"

if (Test-Path -LiteralPath $cargoBin) {
    $env:PATH = "$cargoBin;$env:PATH"
}

if (-not (Get-Command link.exe -ErrorAction SilentlyContinue)) {
    $vswhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vswhere)) {
        throw "Visual Studio Installer or link.exe is required"
    }
    $visualStudio = (& $vswhere -latest -products * -requires `
        Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath).Trim()
    if (-not $visualStudio) {
        throw "install the Visual Studio Desktop development with C++ workload"
    }
    $vsDevCmd = Join-Path $visualStudio "Common7\Tools\VsDevCmd.bat"
    $developmentEnvironment = & cmd.exe /s /c (
        "`"$vsDevCmd`" -arch=x64 -host_arch=x64 >nul && set"
    )
    if ($LASTEXITCODE -ne 0) {
        throw "failed to initialize the Visual Studio C++ environment"
    }
    foreach ($line in $developmentEnvironment) {
        $name, $value = $line -split "=", 2
        if ($name -and $null -ne $value) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

if (-not $Destination) {
    $Destination = Join-Path $projectRoot ".tools\codex-no-tools"
}
$Destination = [System.IO.Path]::GetFullPath($Destination)
$sourcePath = Join-Path $Destination "source"
$binaryPath = Join-Path $Destination "codex-exec.exe"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git is required"
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "cargo is required; install the Rust stable MSVC toolchain first"
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "python is required"
}

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
if (-not (Test-Path -LiteralPath $sourcePath)) {
    git clone --depth 1 --branch $tag https://github.com/openai/codex.git $sourcePath
    if ($LASTEXITCODE -ne 0) {
        throw "failed to clone Codex source"
    }
}

Push-Location $sourcePath
try {
    $actualCommit = (git rev-parse HEAD).Trim()
    if ($actualCommit -ne $commit) {
        throw "unexpected source commit: $actualCommit"
    }

    git apply --reverse --check $patchPath 2>$null
    if ($LASTEXITCODE -ne 0) {
        git apply --check $patchPath
        if ($LASTEXITCODE -ne 0) {
            throw "source tree is neither clean nor already patched"
        }
        git apply $patchPath
        if ($LASTEXITCODE -ne 0) {
            throw "failed to apply no-tools patch"
        }
    }
    git diff --check
    if ($LASTEXITCODE -ne 0) {
        throw "patched source failed git diff --check"
    }

    if ($env:OS -eq "Windows_NT") {
        $targetPath = Join-Path $sourcePath "codex-rs\target"
        New-Item -ItemType Directory -Force -Path $targetPath | Out-Null
        $targetJunction = Join-Path $env:LOCALAPPDATA "Temp\codex-no-tools-target"
        if (-not (Test-Path -LiteralPath $targetJunction)) {
            New-Item -ItemType Junction -Path $targetJunction `
                -Target $targetPath | Out-Null
        }
        $env:CARGO_TARGET_DIR = $targetJunction
    }

    Push-Location (Join-Path $sourcePath "codex-rs")
    try {
        cargo test -p codex-features
        if ($LASTEXITCODE -ne 0) {
            throw "codex-features tests failed"
        }
        cargo build --release -p codex-exec --bin codex-exec
        if ($LASTEXITCODE -ne 0) {
            throw "Codex build failed"
        }
    }
    finally {
        Pop-Location
    }

    Copy-Item -LiteralPath (
        Join-Path $sourcePath "codex-rs\target\release\codex-exec.exe"
    ) -Destination $binaryPath

    $buildInfo = [ordered]@{
        mode = "answer_only"
        binary_sha256 = (Get-FileHash -Algorithm SHA256 $binaryPath).Hash.ToLower()
        cargo = (& cargo --version).Trim()
        codex_commit = $commit
        codex_tag = $tag
        patch_sha256 = (Get-FileHash -Algorithm SHA256 $patchPath).Hash.ToLower()
        rustc = (& rustc --version).Trim()
    }
    $buildInfo | ConvertTo-Json | Set-Content -LiteralPath (
        Join-Path $Destination "build-info.json"
    ) -Encoding utf8
}
finally {
    Pop-Location
}

& python (Join-Path $PSScriptRoot "verify_request.py") `
    --codex-command $binaryPath `
    --model "gpt-5.6-sol"
if ($LASTEXITCODE -ne 0) {
    throw "answer-only request verification failed"
}

& $binaryPath --version
Write-Output "Built patched Codex exec: $binaryPath"

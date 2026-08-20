$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$patchedExecutable = Join-Path $projectRoot ".tools\codex-no-tools\codex-exec.exe"
$stockExecutable = (Get-Command codex -ErrorAction Stop).Source
$workingDirectory = Join-Path $env:TEMP "codex-answer-only-measure"
$outputPath = Join-Path $PSScriptRoot "latest-measurement.json"
$hostContextVariables = @(
    "CODEX_CI",
    "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
    "CODEX_PERMISSION_PROFILE",
    "CODEX_SESSION_ID",
    "CODEX_THREAD_ID"
)

if (-not (Test-Path -LiteralPath $patchedExecutable)) {
    throw "patched codex-exec not found; run build.ps1 first"
}

New-Item -ItemType Directory -Force -Path $workingDirectory | Out-Null
$savedEnvironment = @{}
foreach ($name in $hostContextVariables) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable(
        $name,
        "Process"
    )
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
}

$commonArguments = @(
    "--json",
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
    "--skip-git-repo-check",
    "--sandbox", "read-only",
    "--color", "never",
    "-c", 'instructions="."',
    "-c", 'model_reasoning_effort="low"',
    "--model", "gpt-5.6-sol",
    "."
)

try {
    Push-Location $workingDirectory
    try {
        $measurements = @()
        foreach ($condition in @("stock", "answer_only")) {
            if ($condition -eq "stock") {
                $executable = $stockExecutable
                $arguments = @("exec") + $commonArguments
            }
            else {
                $executable = $patchedExecutable
                $arguments = $commonArguments[0..($commonArguments.Count - 2)] + @(
                    "-c", "features.answer_only=true",
                    "-c", "suppress_unstable_features_warning=true",
                    "."
                )
            }

            $events = & $executable @arguments |
                ForEach-Object { $_ | ConvertFrom-Json }
            if ($LASTEXITCODE -ne 0) {
                throw "$condition measurement failed"
            }
            $completed = $events |
                Where-Object { $_.type -eq "turn.completed" } |
                Select-Object -Last 1
            if ($null -eq $completed) {
                throw "$condition did not emit turn.completed"
            }
            $measurements += [ordered]@{
                condition = $condition
                executable_sha256 = (
                    Get-FileHash -Algorithm SHA256 $executable
                ).Hash.ToLower()
                input_tokens = $completed.usage.input_tokens
                cached_input_tokens = $completed.usage.cached_input_tokens
                output_tokens = $completed.usage.output_tokens
                reasoning_output_tokens = $completed.usage.reasoning_output_tokens
                version = (& $executable --version).Trim()
            }
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    foreach ($name in $hostContextVariables) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $savedEnvironment[$name],
            "Process"
        )
    }
}

$stockInput = $measurements[0].input_tokens
$answerOnlyInput = $measurements[1].input_tokens
$result = [ordered]@{
    measured_at = (Get-Date).ToUniversalTime().ToString("o")
    model = "gpt-5.6-sol"
    reasoning_effort = "low"
    prompt = "."
    measurements = $measurements
    input_token_reduction = $stockInput - $answerOnlyInput
    input_token_reduction_percent = [math]::Round(
        100 * ($stockInput - $answerOnlyInput) / $stockInput,
        2
    )
}
$result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $outputPath -Encoding utf8
$result | ConvertTo-Json -Depth 5

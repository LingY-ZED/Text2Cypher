param(
    [string]$Revision = "error-recovery",
    [ValidateRange(1, 20)]
    [int]$Runs = 3,
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$repository = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repository ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "未找到项目虚拟环境 Python：$python"
}

$revisionSha = (& git -C $repository rev-parse "$Revision`^{commit}").Trim()
if ($LASTEXITCODE -ne 0 -or -not $revisionSha) {
    throw "无法解析 Git revision：$Revision"
}

if (-not $OutputDir) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputDir = Join-Path $repository "tmp\evaluation\$timestamp"
} elseif (-not [IO.Path]::IsPathRooted($OutputDir)) {
    $OutputDir = Join-Path $repository $OutputDir
}
$resolvedOutput = [IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null

$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) "text2cypher-evaluation-worktrees"
New-Item -ItemType Directory -Force -Path $temporaryRoot | Out-Null
$worktree = Join-Path $temporaryRoot ([guid]::NewGuid().ToString("N"))
$resolvedTemporaryRoot = [IO.Path]::GetFullPath($temporaryRoot)
$resolvedWorktree = [IO.Path]::GetFullPath($worktree)
if (-not $resolvedWorktree.StartsWith($resolvedTemporaryRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "临时 worktree 路径越出预期目录"
}

$environmentNames = @(
    "PYTHONPATH",
    "PYTHONUTF8",
    "TEXT2CYPHER_FEW_SHOT_ENABLED",
    "TEXT2CYPHER_QUESTION_DECOMPOSITION_ENABLED",
    "TEXT2CYPHER_CYPHER_CORRECTION_ENABLED",
    "TEXT2CYPHER_EMPTY_RESULT_CORRECTION_ENABLED",
    "TEXT2CYPHER_RETRY_ENABLED",
    "TEXT2CYPHER_RETRY_MAX_ATTEMPTS",
    "TEXT2CYPHER_LLM_DISABLE_THINKING"
)
$savedEnvironment = @{}
foreach ($name in $environmentNames) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

$worktreeAdded = $false
$exitCode = 2
try {
    & git -C $repository worktree add --detach $resolvedWorktree $revisionSha
    if ($LASTEXITCODE -ne 0) {
        throw "无法创建 detached worktree"
    }
    $worktreeAdded = $true

    $env:PYTHONPATH = "$(Join-Path $resolvedWorktree 'src');$repository"
    $env:PYTHONUTF8 = "1"
    $env:TEXT2CYPHER_FEW_SHOT_ENABLED = "true"
    $env:TEXT2CYPHER_QUESTION_DECOMPOSITION_ENABLED = "true"
    $env:TEXT2CYPHER_CYPHER_CORRECTION_ENABLED = "true"
    $env:TEXT2CYPHER_EMPTY_RESULT_CORRECTION_ENABLED = "true"
    $env:TEXT2CYPHER_RETRY_ENABLED = "true"
    $env:TEXT2CYPHER_RETRY_MAX_ATTEMPTS = "3"
    $env:TEXT2CYPHER_LLM_DISABLE_THINKING = "true"

    Push-Location $repository
    try {
        & $python -m evaluation.run `
            --output $resolvedOutput `
            --runs $Runs `
            --revision $Revision `
            --revision-sha $revisionSha
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $savedEnvironment[$name],
            "Process"
        )
    }
    if ($worktreeAdded) {
        & git -C $repository worktree remove --force $resolvedWorktree
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "临时 worktree 清理失败：$resolvedWorktree"
        }
    }
}

Write-Host "评测报告：$(Join-Path $resolvedOutput 'report.md')"
exit $exitCode

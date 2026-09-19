$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "The project virtual environment was not found."
}

$originalPath = $env:PATH
try {
    # Codex desktop may add document/PDF helper DLL directories to PATH. Those
    # unrelated ICU binaries conflict with Qt when PyInstaller discovers them.
    $cleanPath = ($originalPath -split ";" | Where-Object {
        $_ -and $_ -notmatch "[\\/]\.cache[\\/]codex-runtimes[\\/]"
    }) -join ";"
    $env:PATH = $cleanPath

    & $python -m PyInstaller --noconfirm --clean (Join-Path $projectRoot "LocalLens.spec")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
}
finally {
    $env:PATH = $originalPath
}

$executable = Join-Path $projectRoot "dist\LocalLens\LocalLens.exe"
if (-not (Test-Path -LiteralPath $executable)) {
    throw "The packaged LocalLens executable was not created."
}

Write-Host "Built: $executable"

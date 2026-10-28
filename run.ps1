param([switch]$Demo, [switch]$Desktop, [int]$WebPort = 8765)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $pythonExe = 'py'
    $entryPoint = 'web_server.py'
    if ($Desktop) { $entryPoint = 'app.py' }
    $appArgs = @('-3.11', $entryPoint)
    if (Test-Path -LiteralPath '.venv\Scripts\python.exe') {
        $pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
        $appArgs = @($entryPoint)
    }
    if ($Demo) { $appArgs += '--demo' }
    if (-not $Desktop) { $appArgs += @('--port', $WebPort) }
    & $pythonExe @appArgs
    if ($LASTEXITCODE -ne 0) { throw 'Aplikasi gagal. Pastikan Python 3.11 dan Tkinter terpasang.' }
} finally {
    Pop-Location
}

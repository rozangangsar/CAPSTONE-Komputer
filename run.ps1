param([switch]$Demo)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $pythonExe = 'py'
    $appArgs = @('-3.11', 'app.py')
    if (Test-Path -LiteralPath '.venv\Scripts\python.exe') {
        $pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
        $appArgs = @('app.py')
    }
    if ($Demo) { $appArgs += '--demo' }
    & $pythonExe @appArgs
    if ($LASTEXITCODE -ne 0) { throw 'Aplikasi gagal. Pastikan Python 3.11 dan Tkinter terpasang.' }
} finally {
    Pop-Location
}

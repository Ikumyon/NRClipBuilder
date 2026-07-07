Set-Location $PSScriptRoot

if (Test-Path ".venv\Scripts\python.exe") {
    Write-Host "Starting application with virtual environment (.venv)..."
    & ".venv\Scripts\python.exe" nrclip_builder.py
} else {
    Write-Host "Virtual environment (.venv) not found. Running with global python..."
    python nrclip_builder.py
}

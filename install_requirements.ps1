Set-Location $PSScriptRoot

if (Test-Path ".venv\Scripts\pip.exe") {
    Write-Host "Installing dependencies into virtual environment (.venv)..."
    & ".venv\Scripts\pip.exe" install -r requirements.txt
} else {
    Write-Host "Virtual environment (.venv) not found. Installing into global Python environment..."
    python -m pip install -r requirements.txt
}

Write-Host "Installation process completed."

# Build a standalone GUI alkemio-recover.exe for Windows (no console window).
# Run this ON Windows (PowerShell). The python.org installer includes Tkinter by
# default, which is required on the BUILD machine. Clients need nothing installed.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path "target_hashes.txt")) {
    Write-Error "target_hashes.txt missing. Run: python gen_hashes.py <csv>"
}

# verify the build Python has Tkinter
python -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "This Python has no Tkinter. Reinstall Python from python.org with the 'tcl/tk and IDLE' option ticked."
}

python -m venv .buildenv
& .\.buildenv\Scripts\Activate.ps1
pip install --upgrade pip pyinstaller

# --windowed -> no console window. On Windows the --add-data separator is ';'.
pyinstaller --onefile --windowed --noconfirm --clean `
  --name alkemio-recover `
  --add-data "target_hashes.txt;." `
  alkemio_cache_recover.py

Write-Host ""
Write-Host "Built: dist\alkemio-recover.exe  (double-click to run -- no terminal)"
Write-Host "Self-check (from a console): .\dist\alkemio-recover.exe --selftest"

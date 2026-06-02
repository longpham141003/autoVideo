$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    powershell.exe -ExecutionPolicy Bypass -File .\setup.ps1
}

Start-Process "http://127.0.0.1:7860"
.\.venv\Scripts\python.exe .\app.py --host 127.0.0.1 --port 7860

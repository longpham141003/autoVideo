$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher 'py' was not found. Install Python 3.10 or newer and try again."
}

function Get-WorkingPython {
    foreach ($Version in @("3.10", "3.11", "3.12")) {
        py -$Version --version *> $null
        if ($LASTEXITCODE -eq 0) {
            return @("py", "-$Version")
        }
    }

    python --version *> $null
    if ($LASTEXITCODE -eq 0) {
        return @("python")
    }

    throw "No working Python 3.10+ interpreter was found."
}

$PythonCommand = Get-WorkingPython

if (-not (Test-Path ".venv")) {
    if ($PythonCommand.Count -gt 1) {
        & $PythonCommand[0] $PythonCommand[1..($PythonCommand.Count - 1)] -m venv .venv
    }
    else {
        & $PythonCommand[0] -m venv .venv
    }
}

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Get-Command espeak-ng -ErrorAction SilentlyContinue)) {
    Write-Warning "espeak-ng was not found in PATH. English may still work, but install espeak-ng if Kokoro reports pronunciation/G2P errors."
}

Write-Host ""
Write-Host "Setup complete."
Write-Host "Try:"
Write-Host ".\.venv\Scripts\python.exe .\tts.py --text `"Hello, this is Kokoro.`" --out outputs\hello.wav"

param(
    [Parameter(Mandatory = $true)]
    [string]$Text,

    [string]$Out = "outputs\speech.wav",
    [string]$Lang = "a",
    [string]$Voice = "af_heart",
    [double]$Speed = 1.0
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "Virtual environment not found. Run .\setup.ps1 first."
}

.\.venv\Scripts\python.exe .\tts.py --text $Text --out $Out --lang $Lang --voice $Voice --speed $Speed

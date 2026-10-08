# One-time setup on Windows. Run from the cofounder folder in PowerShell:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   (once, if scripts are blocked)
#   .\scripts\install.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Install Python 3.11 or 3.12 from https://www.python.org/downloads/ (tick 'Add to PATH'), then re-run." -ForegroundColor Yellow
    exit 1
}
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "Install Ollama from https://ollama.com/download/windows, then re-run." -ForegroundColor Yellow
    exit 1
}

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
# voice = speech + Silero VAD, brain = multilingual memory, notify = Windows pop-ups + phone push, widget = always-on-top window
.\.venv\Scripts\python.exe -m pip install -e ".[voice,brain,notify,widget,dev]"

ollama pull qwen2.5:7b-instruct

.\.venv\Scripts\sz.exe init
Write-Host ""
Write-Host "Done. Next: edit config.toml and .env, then run:  .\.venv\Scripts\sz.exe auth outlook  and  .\.venv\Scripts\sz.exe doctor" -ForegroundColor Green

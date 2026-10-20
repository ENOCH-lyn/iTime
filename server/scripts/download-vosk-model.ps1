# Download the local Chinese Vosk model used by /api/v1/ai/voice/transcribe.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$models = Join-Path $root "models"
$target = Join-Path $models "vosk-model-small-cn-0.22"
if (Test-Path (Join-Path $target "am")) {
    Write-Host "Vosk model already present: $target"
    exit 0
}
New-Item -ItemType Directory -Force $models | Out-Null
$zip = Join-Path $models "vosk-model-small-cn-0.22.zip"
Invoke-WebRequest -Uri "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip" -OutFile $zip
Expand-Archive -Path $zip -DestinationPath $models -Force
Remove-Item $zip -Force
Write-Host "Installed: $target"
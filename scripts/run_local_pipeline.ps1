param([switch]$Publish)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
python -m scripts.score_fitted_week
if ($LASTEXITCODE -ne 0) { throw 'Fitted prediction scoring failed.' }
python -m scripts.export_model_dashboard
if ($LASTEXITCODE -ne 0) { throw 'Model dashboard export failed.' }
python scripts\ollama_supabase_worker.py
if ($LASTEXITCODE -ne 0) { throw 'Supabase/Ollama review failed.' }
if ($Publish) {
  python -m scripts.publish_fitted_predictions --apply
  if ($LASTEXITCODE -ne 0) { throw 'Prediction publication failed; no further action taken.' }
}
Write-Host 'Local pipeline complete.'
if ($Publish) { Write-Host 'Fitted prediction updates were published and verified.' }
else { Write-Host 'Review complete. No Supabase prediction rows were changed.' }

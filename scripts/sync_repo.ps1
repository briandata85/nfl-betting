param([switch]$Push)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Run-Git([string[]]$GitArgs) {
  & git @GitArgs
  if ($LASTEXITCODE -ne 0) { throw "git $($GitArgs -join ' ') failed" }
}

$protected = @('.env.local','data','outputs','work')
$allowedPrefixes = @('scripts/','tests/','.github/','README.md','ODDS_COLLECTOR.md','SYNC_WORKFLOW.md','.gitignore')
$status = @(git status --porcelain)
$unsafe = @($status | Where-Object {
  $line = $_.ToString()
  $path = $line.Substring(3).Trim('"') -replace '\\','/'
  $protected -notcontains ($path.Split('/')[0]) -and -not ($allowedPrefixes | Where-Object { $path -eq $_ -or $path.StartsWith($_) })
})
if ($unsafe.Count) {
  Write-Error "Uncommitted files outside the protected local folders were found. Review git status before syncing:`n$($unsafe -join "`n")"
}

Run-Git @('fetch','origin','main')
$ahead = [int]((& git rev-list --count 'origin/main..HEAD').Trim())
$behind = [int]((& git rev-list --count 'HEAD..origin/main').Trim())
if ($behind -gt 0) {
  if ($status.Count) { Run-Git @('stash','push','-u','-m','automated local research backup') }
  Run-Git @('rebase','origin/main')
  if ($status.Count) { Run-Git @('stash','pop') }
}
python -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) { throw "Could not install repository dependencies; no push performed." }
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw "Repository tests failed; no push performed." }
Write-Host "Repository checks passed. Local research folders were preserved. Ahead: $ahead; behind before sync: $behind"
if ($Push) {
  Run-Git @('push','origin','main')
  Write-Host 'Pushed the already committed allowlisted changes to origin/main.'
} else {
  Write-Host 'No push performed. Add -Push when you want to publish committed changes.'
}

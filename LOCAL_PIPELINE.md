# Local NFL pipeline

From PowerShell in the repository:

```powershell
powershell.exe -ExecutionPolicy Bypass -File ".\scripts\run_local_pipeline.ps1"
```

This refreshes the fitted Week 1 predictions, rebuilds the local model comparison snapshot, reads Supabase predictions, and asks WSL Ollama for a review. It does not write prediction rows.

To publish the prepared fitted updates, use:

```powershell
powershell.exe -ExecutionPolicy Bypass -File ".\scripts\run_local_pipeline.ps1" -Publish
```

The publish step only updates eligible upcoming FanDuel spread rows with matching archived quotes and verifies each response. It skips started or settled rows. Never add `.env.local`, `data/`, `outputs/`, or `work/` to Git.

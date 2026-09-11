# Safe repository sync

Run from PowerShell in the repository:

```powershell
.\scripts\sync_repo.ps1
```

The script fetches `origin/main`, safely rebases when the remote is ahead, runs the test suite, and preserves `.env.local`, `data/`, `outputs/`, and `work/` as local-only folders. It refuses to proceed when other uncommitted files are present. It does not push by default.

After reviewing the test result and `git status`, publish already committed changes with:

```powershell
.\scripts\sync_repo.ps1 -Push
```

The script does not create commits or decide which source changes should be published. That boundary keeps Ollama from accidentally committing credentials, generated outputs, or unrelated work.

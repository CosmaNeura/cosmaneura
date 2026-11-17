# Rerun only failed GitHub Actions jobs

Do not automate CI reruns. When a Pull Request Checks (or other) run fails and
you need another attempt of only the failed jobs:

```bash
gh run rerun --failed <run-id>
```

Use the run id from the Actions URL or `gh run list`. Prefer fixing the head and
pushing a new commit when the failure is caused by source under review; use
`--failed` for flake or infrastructure noise on an otherwise correct SHA.

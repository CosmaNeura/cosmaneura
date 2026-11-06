# Archived from CLAUDE.md — QA bots overlap

## QA bots

QA covers these repositories:

- `CosmaNeura/cosmaneura` (Core)
- `cosmaneura-scribe`
- `cosmaneura-web`
- `cosmaneura-os` (Cosmas OS / CosmaBot)

### How a handoff runs

QA runs through GitHub issues labeled `qa-handoff`, one issue per QA bot.

- Current bot: QA Tester Bot, label `qa-bot-1`, cases `QA-100` through
  `QA-199`.
- `QA-200` and above are reserved for a future bot 2.

The coordinator posts `HANDOFF` pinned to the full source commit, deployed
revision or build ID, and immutable artifact digest for each component under
test; use the image digest for container workloads. Verify that identity using
release evidence matched to read-only running-workload metadata.
`GET /healthcheck` checks availability only: it returns `{"status":"ok"}`, not a
build or commit. A healthy response or the current repository tip does not
establish the deployed identity. If that identity cannot be verified, report
`BLOCKED`. The bot posts `ACK`, runs its cases, and posts `RESULT` (`PASS`, `FAIL`,
or `BLOCKED`) with uploaded evidence.

### Practice and shared tools

Each bot has its own login and its own test practice. A bot leaves every other
bot's practice and the shared demo site untouched. Shared tools (auth, settings
export) are owned by one bot at a time. The owning bot posts `RELEASED` in the
issue when it is done.

### Targets

- Exercise `https://staging-app.cosmaneura.com` only.
- On production `https://app.cosmaneura.com`, a page-load check is the only
  allowed check. No login and no actions.

### Hard rules

- No production logins and no production deploys.
- Never run `Deploy Staging Candidate` or any deploy or rollback workflow.
- QA bots post results only on handoff issues. They do not comment on, approve,
  or merge pull requests.
- Synthetic data only. No PHI.
- Stripe test mode only.
- No real clinician Zoom.
- Never contact clinicians or MCD.
- Sign-in and Zoom-join results are status-only. They are never called
  "fixed".

### Known limits

- PR checks use GitHub-hosted runners; workflow enablement is a separate setting.
- Stripe checkout may fail because of a Clerk permission issue.
- Recording-start timing needs a real iPhone and is out of scope.

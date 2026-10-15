# Agent coordination

Read before editing. Claim files in a dated entry; edit only your own entries.
Use an isolated worktree if claims overlap. Never deploy uncommitted work.

- **Codex: two-bot QA loop implementation, local handoff ready.** 2026-10-15 UTC.
  Holds: `qa/two-bot/`, `.github/ISSUE_TEMPLATE/qa-finding.md`,
  `docs/flight-plans/two-bot-qa-loop.md`, this entry and the QA addition to `AGENTS.md`.
  Commit: see `git log chore/two-bot-qa-loop` for the implementation commit.
  Tests: 13 stdlib regressions passed; Ruff, queue validation and diff checks passed.
  Review: `qa/two-bot/REVIEW.md`. Deployed: not deployed.
  Left: choose bot identities and isolated targets, configure queues, run manual pilot,
  then authorize scheduling. PR review must bind the final pushed SHA.
  Files released: yes.

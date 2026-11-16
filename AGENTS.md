# AGENTS.md — Cosmas / CosmaNeura core

Hard rules for agents in this repo. Longer process, risk tiers, promotion
mechanics, and QA handoff detail live under `docs/reference/archive/` (and the
architecture deploy-kit). Do not copy CosmaBot’s freer production authority here.

## Hard rules

1. **Staging is the default** for agent builds, tests, and environment work.
2. **Production deploy or environment changes need John’s yes.**
3. **Synthetic data only.** No patient data / PHI in tests, fixtures, logs, or prompts.
4. **Small PRs.** No force-push to `master`.
5. **Red machine checks are not waved away** with local notes or prose.
6. **GitHub Actions stay on** (standing order 5 Nov 2026). Do not turn Actions
   off or reintroduce an Actions-off rule.

## Alone

- Create branches, write code, run synthetic tests, open draft PRs, fix CI.
- Use **staging** hosts, staging sign-in, and staging config only.
- Read-only inspection of live staging or Core state remains allowed without a
  separate production decision.
- Agents never invoke production deploy, production migration, or production
  Azure mutation commands.

## Needs John’s yes

Staging Azure mutation and `Deploy Staging Candidate` are allowed only when
John explicitly authorizes that exact staging action in the current session.
There is no standing or global permission. Authorization never covers
production or PHI. Founder-only, protected-master, immutable-candidate, and
protected-credential gates remain mandatory.

Also needs John’s yes:

- Production package, promote, deploy, or infra apply.
- Live login / production environment identity changes.
- Real patient data, destructive data work, or turning off safety checks.
- Broadening release cohorts or adding external production dependencies.

## Never

- Commit secrets; put PHI in the tree; merge PRs; force-push `master`.
- Touch CosmaBot (`cosmaneura-os`) or Codex’s branches.
- Weaken or disable existing CI; change production without John’s yes.
- Treat agent output as GitHub approval.

## Local commands

`just setup` · `just py_test && just js_test` · `just lint` · `just dev` ·
`uv run pytest <path>` · `just db_generate_migration "<name>"` · `just db_migrate`

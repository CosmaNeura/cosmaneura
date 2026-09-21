# cosmaneura

This is the primary repo for cosmaneura.com.

[Generated from python-starter-template.](http://github.com/iloveitaly/python-starter-template)

Architecture source of truth: the `cosmaneura-architecture` repository (`ARCHITECTURE.md`, `adr/`, `contracts/`). Any Google Doc overview is historical/summary only and is not authoritative.

## Deployment

* The top-level [Justfile](./Justfile) contains all of build scripts (`just build` will do it all for you)
* `infra/` contains all terraform logic. `cd infra && just install && just azure_apply` will setup infrastructure.
- `master` is the only long-lived branch. Staging and production are environments promoted to an explicit reviewed `master` SHA; merging never deploys.
- Pull requests have a required `change-budget` check that rejects more than 2,000 changed lines or 50 changed files. The repository ruleset enforces that exact context without changing the current human-approval requirement.
- Each environment has its own founder-started workflow, and both run from `master`:
  - Production: `Build & Publish (manual)`, dispatched on `master`. It requires the Terraform apply run ID and the provider-signed Recall callback attestation.
  - Staging: `Deploy Staging Candidate`, which takes an explicit `candidate_sha` contained in protected `master` and deploys that candidate rather than the moving tip.
- A manual run is a real deployment, not a dry run. Before starting one, compare the target environment's deployed SHA, configuration, and schema with the candidate and resolve any drift deliberately. This source gate does not prove that staging is synthetic-only or non-PHI.

### Staging-rehearsal base recovery

The base-recovery apply is a separate founder-operated incident procedure, not
a deployment workflow. After a fresh serial-8 plan and GET-only handoff receipt
have been independently reviewed, the operator may use
`scripts/consume_staging_rehearsal_base_recovery_handoff.py` from the exact
current protected `master` commit. The consumer accepts only the four external
digests/identities documented by its `--help`, requires five exact entries on a
foreground controlling terminal—including the exact 28-no-op/11-create
statement—then repeats remote-state and complete ARM readback. It pins the
reviewed plan to an inherited descriptor. The post-confirmation ARM pass resets
its isolated response-date and byte accounting but inherits the ceremony's
already-running 120-second absolute deadline. The consumer then claims the
handoff once and passes only `/dev/fd/<n>` to OpenTofu. Never invoke it from CI,
an agent, a wrapper, or a noninteractive shell. A failed run after the claim
file exists requires a new plan, execution directory, and handoff.

---

## Suggested Consent Script

> I’m using a new tool to take better notes during our appointment so I can focus fully on you. It requires recording our conversation, but this is entirely optional. The recording is securely stored, follows strict HIPAA guidelines, and is deleted within a few hours after use. Are you okay with me recording?

## LiveSwitch Process

1. In LiveSwitch, start or join a session.
2. Click the Record button
3. Stop the recording, then leave the call.
4. In LiveSwitch’s main dashboard, go to the Recordings tab.
5. Locate your new recording (it will take a while to process, at least 10m for larger videos, could take hours depending on their system).
6. Click “Actions” → “Download” to save the file to your computer.
7. Open the Cosmaneaura app’s Upload page.
8. Select the downloaded file and upload it.
9. Wait for the “Upload successful” message, then check the notes after processing completes.

## PHI Management

Here are the rules we use to manage PHI. You must follow these rules:

- Never store PHI on your machine.
- Prefer maintaining a manually curated, high-quality de-identified data set for all development and testing to reduce security overhead and ensure compliance.
- Utilize Azure’s [De-identification API](https://learn.microsoft.com/en-us/azure/healthcare-apis/deidentification/overview) to anonymize PHI, simplifying security requirements by removing PHI classification.
- Restrict data access to authorized personnel, encrypt data both in transit and at rest using strong algorithms, and continuously monitor data usage with regular security audits.

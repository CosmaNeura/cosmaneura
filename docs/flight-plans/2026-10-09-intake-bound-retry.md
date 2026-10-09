# Intake Bound Retry

Tier 2: existing public patient-answer write path. No auth, consent, billing,
schema, retention or deployment changes. The owner accepted the review finding
and requested testing, deconfliction and merge in this chat.

The receiver accepts optional expected_question and advertises retry_safe.
Under a refreshed PostgreSQL row lock, an exact replay of the unique question
and answer returns current progress without another write or summarize job.
A different answer or unknown/noncurrent question is refused. Legacy first
submissions remain compatible and participate in the same lock. No answer or
question is added to logs.

The browser supplies its shown question and retries only when both its initial
loader and recovery GET advertise the guard. With an older receiver it may read
progress but never resend an ambiguous answer. Receiver and browser ship in the
same repository; receiver compatibility remains mandatory if deploys differ.

Qualification: synthetic route tests, retry/replay regressions, full frontend
suite, types, lint and change budget. No live patient/service calls. Recovery:
revert the PR merge commit; the old browser has no new guarded-retry capability.
There is no database migration or data rollback.

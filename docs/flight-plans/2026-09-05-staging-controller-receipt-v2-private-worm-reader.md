# Flight plan: dormant controller-receipt-v2 private-WORM reader

- Tier **2** adds a dormant one-shot reader for the exact intent, submission, and controller receipt selected only by sealed launch authority. The carrier asserts only `record_source_authenticated=true`; provider facts, writer/private-route proof, and runtime authority remain false.
- No caller selector is accepted; bounded sweep refuses pre-I/O. Producer, provider observation, runtime consumer, workflow, infrastructure, credentials, deployment, restore, and live-cloud work are excluded.
- Dependency: receipt-v2 contract at master `67ad3fd1b44f0b087ea959a0934d0b7454adb67c`. Exact controller, safe-submit, envelope, and runtime pins plus shared account/host/container/endpoint/identity bindings are checked before I/O.
- Role/WORM identifiers and digests are structurally configured but RBAC/immutability configuration remains an unproven activation prerequisite; each GET authenticates its returned WORM headers. `record_order` is logical chain/hash/read order, not provider write chronology.
- One selected-job token and three exact GETs are allowed: no proxy, redirect, listing, retry, polling, or disk write. Exact framing, MD5, strong ETag, BlockBlob, encryption, Locked retention, legal hold, and non-regressing clocks are required.
- Authority, retained policies, environment, and deadline are rechecked between stages; the reader burns on entry and emits bounded errors without record material.
- All activation/producer/observation/consumer/workflow flags remain false; only private-reader implementation is true. Recovery is code revert; schema/data/infra/deployed behavior are N/A.
- Completion requires focused/adjacent tests, Ruff, Pyright, compileall, policy loaders/hashes, budget, and independent audit. Activation remains a later reviewed integration and approved rehearsal.

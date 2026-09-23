# Staging probe publication: exact OCI input

## Outcome and risk

Tier 2. Add a read-only validator for the already independently reviewed,
synthetic runtime-capability probe archive. This is the byte-preserving input
boundary for a later private publisher, not a publisher, execution command,
image-provenance authenticator or deployment approval.

The existing runtime-image authenticator deliberately lacks a concrete
publication transport. Do not repurpose its dormant receipt as authorization
for this diagnostic image. Private image transfer, its narrow trust contract,
private-network path, credentials, live registry behavior and exact human
plan/handoff remain separate work.

## Exact input contract

`inspect_probe_archive` accepts immutable archive bytes and three expected
pins: archive SHA256, manifest digest and config digest. The caller must obtain
those pins from independent image review, never self-derive them from unknown
input and call that trusted. All failures use fixed error codes.

The validator:

- Checks the complete tar hash, including framing and padding, before parsing.
- Pre-scans every physical USTAR header/payload before the logical tar reader:
  unsigned checksums, unambiguous name/size fields, bounded physical member count,
  no prefix/link/extension records, zero payload padding, two complete end markers
  and only zero padding through the exact end. GNU/PAX and legacy non-USTAR
  formats are deliberately unsupported by this selected-archive profile.
- Accepts bounded regular OCI layout/index/blob files, optional matching Docker
  compatibility metadata, and the two standard blob directories only.
- Rejects duplicate names/JSON keys, links, special files, sparse/PAX entries,
  paths outside the layout, extra blobs, tags, foreign URLs and unknown descriptor
  fields. A single OCI image manifest is selected, not a multi-platform index.
- Checks exact manifest/config pins and every descriptor's compressed bytes,
  SHA256 and size. The returned immutable blobs retain the original bytes;
  neither JSON normalization nor media-type conversion occurs.
- Requires linux/amd64, UID/GID65532, the fixed isolated Python entrypoint and
  working directory, no command/volume/port override, and a matching diff-id
  count. These structural checks do not replace full image inspection.
- Performs no layer decompression/extraction, filesystem read/write, network,
  credential discovery or execution. There is no command-line entrypoint.
- Provides exact-byte readback comparison for a future transfer adapter. A
  digest header or semantically equivalent JSON cannot pass in place of bytes.

The parser caps archive bytes at64MiB, members at24, layers at12, and individual
JSON documents at128KiB. The returned inventory explicitly reports publication,
execution, runtime acceptance and activation as false; it is not a receipt.

## Verification actually performed

98 synthetic unit cases were added across the initial75 and23 corrective cases.
The initial75 passed before review. After the physical-stream correction, all23
new cases and seven affected existing cases passed across focused batches; final
full-suite CI is still required. The unchanged initial suite was not replayed.
They cover successful byte retention, exact
readback, independent pin mismatch, padding/framing changes, malformed archives,
special nodes, duplicate/unsafe paths and JSON, foreign descriptors, unsupported
configuration and resource limits. They never execute an image or use a registry.
The corrective cases repin their modified tar bytes: trailing nonzero data,
concatenated archives, missing end markers, hidden GNU/PAX/sparse records and
1200-record extension chains, payload padding, header ambiguity/checksum and
fixed recursion failures. The retained image also passed the new physical scan.
Ruff0.11.6 lint/format and targeted Pyright1.1.399 Linux checks pass. The local
typecheck explicitly uses the existing test interpreter; its initial missing-
pytest environment diagnostic was resolved without changing source or rerunning
the passing suite.

The new validator was also run read-only against the retained reviewed archive:

- Archive: `2859230e2a0fc2232b0f34ca90adec1c83ce94e81ff2c13dd8b2a9d4c0f5561b`.
- Manifest: `sha256:66c56cb1090d04483fc04bc18e61c68a6d3dedefd090a146d4ff7419a30e7233`.
- Config: `sha256:e8abc9f994ac32f533c33a464683e5228925493891cc37360d0184af6e343a08`.
- All six compressed layers verified, totaling17,952,948bytes. No rebuilding,
  conversion, extraction, decompression, upload or image execution.

This additional component check is not a second independent image review.
Uncompressed diff-ids, filesystem contents, image supply chain and runtime
behavior remain outside this parser's claim. The earlier independent full-image
review and its limitations are preserved.

The new module is included in the existing infrastructure-general CI shard,
which runs the infrastructure directory except its explicit specialist globs.
No workflow or dependency was added. Require final-head CI and durable exact
independent source review before normal merge; agent review is not GitHub approval.

Initial independent review23 found oneP2: logical tar iteration alone did not
cover hidden physical records or bytes beyond the end marker. The physical
pre-scan and adversarial cases above are the implementer's correction; they
require fresh final-head independent review. Do not reuse the earlier verdict.

## Next integration and live gates

Bind an independently reviewed transfer tool and private-network publisher to
the exact archive and registry/repository. Preserve all bytes and compare the
registry's exact manifest/config/layer readback. No public ACR exposure, public
mirror, trust bypass, broad reuse of recovery identities or new billable resource
is authorized by this code. The currently considered transfer-tool release is
not installed or invoked by this change.

Any unknown/failed upload must remain unknown/failed: do not turn this validator
into automatic retry, cleanup, publication acceptance or runtime authority.
Actual publication needs reviewed network/identity/cost choices and fresh exact
human plan/handoff evidence. Do not reuse consumed or expired foundation artifacts.

## Conditional controls and recovery

No user-visible feature flag, schema/data migration or cross-repository rollout
applies. This dormant library changes no existing consumer or activation gate.
Before integration, recover by normally reverting these three added files.
After integration, review dependent code first; no registry/image deletion is
part of recovery. No deployment, infrastructure apply or live acceptance occurred.

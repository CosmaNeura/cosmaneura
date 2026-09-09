#!/opt/homebrew/bin/python3 -IS
# ruff: noqa: I001
"""Consume one exact staging-rehearsal base-recovery handoff, once.

This is a human-only final boundary.  It repeats every local, remote-state, and
live Azure check before replacing itself with the exact reviewed OpenTofu apply.
It must never be invoked by automation or by an agent.
"""

# fmt: off
from __future__ import annotations
import sys as _bootstrap_sys
if __name__ == '__main__' and (_bootstrap_sys.flags.isolated != 1 or _bootstrap_sys.flags.no_site != 1 or not _bootstrap_sys.flags.safe_path):
    _bootstrap_sys.stderr.write('recovery_apply_refused\n')
    raise SystemExit(2)
import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import select
import stat
import sys
import time
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

_HANDOFF_FILE = Path(__file__).resolve().with_name('prepare_staging_rehearsal_base_recovery_handoff.py')
try:
    handoff = types.ModuleType('_cosmaneura_recovery_handoff')
    handoff.__file__ = str(_HANDOFF_FILE)
    sys.modules[handoff.__name__] = handoff
    exec(compile(_HANDOFF_FILE.read_bytes(), str(_HANDOFF_FILE), 'exec', dont_inherit=True), handoff.__dict__)
    recovery = handoff.recovery
except Exception:
    print('recovery_apply_refused', file=sys.stderr)
    raise SystemExit(2)

MAX_RECEIPT_BYTES = 16 * 1024
MAX_PROMPT_BYTES = 256
CONFIRMATION_SECONDS = handoff.MAX_ACTION_READBACK_SECONDS
CLAIM_NAME = '.base-recovery-apply-claimed-v1'
NONCE_NAME = '.base-recovery-handoff-nonce'
POLICY_ID = 'cosmaneura-staging-rehearsal-base-recovery-handoff-v1'
HASH_RE = re.compile(r'^[0-9a-f]{64}$')
COMMIT_RE = re.compile(r'^[0-9a-f]{40}$')
TIMESTAMP_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$')
RECEIPT_FIELDS = frozenset({
    'schema_version', 'policy_id', 'source_commit', 'source_tree_sha256',
    'handoff_source_sha256', 'provider_lock_sha256', 'state_lineage',
    'state_serial', 'state_json_sha256', 'remote_state_sha256',
    'saved_plan_sha256', 'derived_plan_json_sha256', 'backend_metadata_sha256',
    'open_tofu_sha256', 'execution_provider_tree_sha256',
    'python_executable_sha256', 'python_runtime_sha256', 'azure_cli_sha256',
    'azure_cli_version_sha256', 'azure_tls_ca_bundle_sha256',
    'azure_config_binding_sha256', 'base_validation_receipt_sha256',
    'machine_binding_sha256', 'nonce_sha256', 'live_snapshot_sha256',
    'observed_at', 'expires_at', 'existing_azure_resource_count',
    'planned_absent_resource_count', 'target_resource_count',
    'live_readback_bound', 'apply_authorized', 'pass',
})
HASH_FIELDS = frozenset(name for name in RECEIPT_FIELDS if name.endswith('_sha256'))
SOURCE_PATHS = (
    recovery.REHEARSAL_ROOT,
    recovery.VALIDATOR_PATH,
    _HANDOFF_FILE,
    Path(__file__).resolve(),
)


class ConsumerError(ValueError):
    """A fail-closed refusal whose detail is never printed by the CLI."""


def _fail(code: str) -> NoReturn:
    raise ConsumerError(code)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _fail('operator.confirmation_timeout')


def _exact_int(value: Any, expected: int) -> bool:
    return type(value) is int and value == expected


def _parse_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or TIMESTAMP_RE.fullmatch(value) is None:
        _fail(f'{label}.invalid')
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError:
        _fail(f'{label}.invalid')
    if parsed.utcoffset() != timedelta(0):
        _fail(f'{label}.invalid')
    return parsed.astimezone(UTC)


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        _fail('clock.invalid')
    return value.astimezone(UTC)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail('receipt.duplicate_key')
        result[key] = value
    return result


def _read_receipt(path: Path, expected_sha256: str) -> tuple[Mapping[str, Any], bytes]:
    recovery._reject_symlink_components(path.parent, label='handoff receipt directory')
    parent = recovery._open_protected_directory(path.parent, label='handoff receipt directory')
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        descriptor = os.open(path.name, flags, dir_fd=parent)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o400 or not 0 < before.st_size <= MAX_RECEIPT_BYTES:
            _fail('receipt.unsafe')
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b''.join(chunks)
        after = os.fstat(descriptor)
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if len(raw) != before.st_size or before_identity != after_identity:
            _fail('receipt.changed')
    except OSError:
        _fail('receipt.unavailable')
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)
    if _digest(raw) != expected_sha256:
        _fail('receipt.digest_mismatch')
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys, parse_constant=lambda _: _fail('receipt.invalid'))
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail('receipt.invalid')
    if not isinstance(value, Mapping) or _canonical(value) + b'\n' != raw:
        _fail('receipt.noncanonical')
    return value, raw


def _metadata_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (metadata.st_dev, metadata.st_ino, metadata.st_uid, stat.S_IMODE(metadata.st_mode), metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


def _identity(path: Path, *, directory: bool=False) -> tuple[int, int, int, int, int, int, int]:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError:
        _fail('artifact.unavailable')
    expected_kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_kind(metadata.st_mode) or metadata.st_uid != os.geteuid() or (not directory and metadata.st_nlink != 1):
        _fail('artifact.unsafe')
    return _metadata_identity(metadata)


def _validate_paths(saved_plan: Path, state_json: Path, execution_dir: Path, receipt_path: Path) -> None:
    paths = (saved_plan, state_json, execution_dir, receipt_path)
    if any(not path.is_absolute() or path != Path(os.path.abspath(path)) for path in paths):
        _fail('path.invalid')
    if len({path.parent for path in paths}) != 1:
        _fail('path.parent_mismatch')
    for path in paths:
        recovery._reject_symlink_components(path, label='consumer input')
    recovery._require_protected_directory(saved_plan.parent, label='artifact parent')
    recovery._require_protected_directory(execution_dir, label='execution directory')
    recovery._read_protected_file(saved_plan, label='saved plan', required_mode=0o400)
    recovery._read_protected_file(state_json, label='state snapshot', required_mode=0o600)
    receipt_identity = _identity(receipt_path)
    if receipt_identity[3] != 0o400 or not 0 < receipt_identity[4] <= MAX_RECEIPT_BYTES:
        _fail('receipt.unsafe')
    recovery._read_protected_file(execution_dir / NONCE_NAME, label='handoff nonce', required_mode=0o400)


def _source_binding(expected_commit: str, expected_tree_sha256: str) -> tuple[Any, str]:
    snapshot = recovery._source_snapshot(expected_commit)
    if snapshot.tree_sha256 != expected_tree_sha256:
        _fail('source.changed')
    relative_paths = [path.relative_to(recovery.REPOSITORY_ROOT) for path in SOURCE_PATHS[1:]]
    status = recovery._run_git(['status', '--porcelain=v1', '--untracked-files=all', '--', *(str(path) for path in relative_paths)])
    if status:
        _fail('source.changed')
    preparer_digest = ''
    for path, relative in zip(SOURCE_PATHS[1:], relative_paths, strict=True):
        value = path.read_bytes()
        if recovery._run_git(['show', f'{expected_commit}:{relative}']) != value:
            _fail('source.changed')
        if path == _HANDOFF_FILE:
            preparer_digest = _digest(value)
    if HASH_RE.fullmatch(preparer_digest) is None:
        _fail('source.changed')
    return snapshot, preparer_digest


def _base_receipt(*, saved_plan: Path, state_json: Path, execution_dir: Path, expected_source_commit: str, expected_saved_plan_sha256: str, now: datetime) -> tuple[Mapping[str, Any], Path]:
    plan = recovery._read_protected_file(saved_plan, label='saved plan', required_mode=0o400)
    state = recovery._read_protected_file(state_json, label='state snapshot', required_mode=0o600)
    source_before = recovery._source_snapshot(expected_source_commit)
    runtime = recovery._snapshot_and_verify_runtime(data_dir=execution_dir)
    tofu = runtime.tofu
    backend_before = recovery._backend_snapshot(tofu=tofu, data_dir=execution_dir)
    result = dict(recovery._validate_captured_artifacts(
        saved_plan_bytes=plan, state_bytes=state, source=source_before,
        backend_metadata_sha256=backend_before, runtime=runtime, data_dir=execution_dir,
        generation_mode='digest_bound_revalidation', now=now,
        expected_saved_plan_sha256=expected_saved_plan_sha256,
    ))
    if recovery._backend_snapshot(tofu=tofu, data_dir=execution_dir) != backend_before or recovery._source_snapshot(expected_source_commit) != source_before:
        _fail('runtime.changed')
    result.update(
        execution_data_dir=str(execution_dir),
        execution_provider_tree_sha256=recovery._provider_tree_sha256(execution_dir),
        open_tofu_path=str(tofu),
        open_tofu_sha256=recovery._executable_sha256(tofu, label='OpenTofu executable'),
    )
    return result, tofu


def _validate_receipt(receipt: Mapping[str, Any], base: Mapping[str, Any], *, expected_source_commit: str, expected_saved_plan_sha256: str, handoff_source_sha256: str, python_binding: Mapping[str, str], now: datetime) -> tuple[datetime, datetime]:
    if set(receipt) != RECEIPT_FIELDS:
        _fail('receipt.schema_mismatch')
    if any(not isinstance(receipt.get(name), str) or HASH_RE.fullmatch(str(receipt[name])) is None for name in HASH_FIELDS):
        _fail('receipt.hash_invalid')
    observed = _parse_timestamp(receipt.get('observed_at'), label='receipt.observed_at')
    expires = _parse_timestamp(receipt.get('expires_at'), label='receipt.expires_at')
    exact = {
        'schema_version': 1,
        'policy_id': POLICY_ID,
        'source_commit': expected_source_commit,
        'source_tree_sha256': base.get('source_tree_sha256'),
        'handoff_source_sha256': handoff_source_sha256,
        'provider_lock_sha256': base.get('provider_lock_sha256'),
        'state_lineage': recovery.EXPECTED_LINEAGE,
        'state_serial': recovery.EXPECTED_RECOVERY_SERIAL,
        'state_json_sha256': base.get('state_json_sha256'),
        'saved_plan_sha256': expected_saved_plan_sha256,
        'derived_plan_json_sha256': base.get('derived_plan_json_sha256'),
        'backend_metadata_sha256': base.get('backend_metadata_sha256'),
        'open_tofu_sha256': base.get('open_tofu_sha256'),
        'azure_cli_sha256': base.get('azure_cli_sha256'),
        'azure_cli_version_sha256': base.get('azure_cli_version_sha256'),
        'execution_provider_tree_sha256': base.get('execution_provider_tree_sha256'),
        'python_executable_sha256': python_binding.get('python_executable_sha256'),
        'python_runtime_sha256': python_binding.get('python_runtime_sha256'),
        'base_validation_receipt_sha256': _digest(_canonical(base)),
        'existing_azure_resource_count': 26,
        'planned_absent_resource_count': 11,
        'target_resource_count': 0,
        'live_readback_bound': True,
        'apply_authorized': False,
        'pass': True,
    }
    if any(receipt.get(name) != value or type(receipt.get(name)) is not type(value) for name, value in exact.items()):
        _fail('receipt.fact_mismatch')
    if not (_exact_int(base.get('existing_resource_count'), 28) and _exact_int(base.get('create_count'), 11) and _exact_int(base.get('update_count'), 0) and _exact_int(base.get('destroy_or_replace_count'), 0)):
        _fail('plan.change_set_mismatch')
    base_exact = {
        'schema_version': 2,
        'policy_id': 'cosmaneura-staging-rehearsal-base-recovery-plan-v2',
        'generation_mode': 'digest_bound_revalidation',
        'source_commit': expected_source_commit,
        'backend_workspace': 'default',
        'state_lineage': recovery.EXPECTED_LINEAGE,
        'state_serial': recovery.EXPECTED_RECOVERY_SERIAL,
        'saved_plan_sha256': expected_saved_plan_sha256,
        'open_tofu_version': recovery.EXPECTED_TOFU_VERSION,
        'open_tofu_source_path': str(recovery.EXPECTED_TOFU_PATH),
        'open_tofu_sha256': recovery.EXPECTED_TOFU_SHA256,
        'azure_cli_source_path': str(recovery.EXPECTED_AZURE_CLI_PATH),
        'azure_cli_sha256': recovery.EXPECTED_AZURE_CLI_SHA256,
        'azure_cli_version_sha256': recovery.EXPECTED_AZURE_CLI_VERSION_SHA256,
        'trusted_runtime_snapshotted': True,
        'validated_nonempty_change_set': True,
        'artifact_policy_complete': True,
        'live_readback_bound': False,
        'apply_authorized': False,
        'full_refresh_generated_here': False,
        'pass': True,
    }
    if any(base.get(name) != value or type(base.get(name)) is not type(value) for name, value in base_exact.items()):
        _fail('plan.authorization_mismatch')
    if not isinstance(base.get('state_semantic_sha256'), str) or HASH_RE.fullmatch(str(base['state_semantic_sha256'])) is None:
        _fail('plan.authorization_mismatch')
    if expires - observed != handoff.RECEIPT_LIFETIME or now < observed - handoff.MAX_CLOCK_SKEW or now >= expires:
        _fail('receipt.expired')
    return observed, expires


def _machine_binding(execution_dir: Path, nonce: bytes, boot: bytes) -> str:
    directory = os.stat(execution_dir, follow_symlinks=False)
    facts = {
        'boot': _digest(boot), 'euid': os.geteuid(),
        'execution_device': directory.st_dev, 'execution_inode': directory.st_ino,
        'nonce_sha256': _digest(nonce),
    }
    return _digest(_canonical(facts))


@dataclass(frozen=True)
class LocalBinding:
    receipt: Mapping[str, Any]
    receipt_bytes: bytes
    base: Mapping[str, Any]
    tofu: Path
    state: Mapping[str, Any]
    nonce: bytes
    observed: datetime
    expires: datetime
    identities: tuple[tuple[int, int, int, int, int, int, int], ...]


def _validate_local(*, saved_plan: Path, state_json: Path, execution_dir: Path, receipt_path: Path, expected_source_commit: str, expected_saved_plan_sha256: str, expected_receipt_sha256: str, python_binding: Mapping[str, str], clock: Callable[[], datetime], boot_session: Callable[[], bytes]) -> LocalBinding:
    _validate_paths(saved_plan, state_json, execution_dir, receipt_path)
    now = _utc_now(clock)
    if _canonical(handoff._python_runtime_binding()) != _canonical(python_binding):
        _fail('python_runtime.changed')
    receipt, receipt_bytes = _read_receipt(receipt_path, expected_receipt_sha256)
    base, tofu = _base_receipt(saved_plan=saved_plan, state_json=state_json, execution_dir=execution_dir, expected_source_commit=expected_source_commit, expected_saved_plan_sha256=expected_saved_plan_sha256, now=now)
    source, preparer_digest = _source_binding(expected_source_commit, str(base.get('source_tree_sha256', '')))
    if source.tree_sha256 != receipt.get('source_tree_sha256'):
        _fail('source.changed')
    observed, expires = _validate_receipt(receipt, base, expected_source_commit=expected_source_commit, expected_saved_plan_sha256=expected_saved_plan_sha256, handoff_source_sha256=preparer_digest, python_binding=python_binding, now=now)
    state_bytes = recovery._read_protected_file(state_json, label='state snapshot', required_mode=0o600)
    if _digest(state_bytes) != base.get('state_json_sha256') or base.get('state_json_sha256') != receipt.get('state_json_sha256'):
        _fail('state_snapshot.changed')
    state = recovery._load_json_bytes(state_bytes, label='state snapshot')
    recovery._validate_state(state, label='state snapshot')
    nonce_path = execution_dir / NONCE_NAME
    nonce = recovery._read_protected_file(nonce_path, label='handoff nonce', required_mode=0o400)
    boot = boot_session()
    if type(boot) is not bytes or not boot or len(boot) > 256 or len(nonce) != 32 or _digest(nonce) != receipt.get('nonce_sha256') or _machine_binding(execution_dir, nonce, boot) != receipt.get('machine_binding_sha256'):
        _fail('machine_binding.changed')
    identities = (
        _identity(saved_plan), _identity(state_json), _identity(receipt_path),
        _identity(nonce_path), _identity(execution_dir, directory=True),
    )
    return LocalBinding(receipt, receipt_bytes, base, tofu, state, nonce, observed, expires, identities)


def _validate_operator_tty() -> int:
    if os.getuid() == 0 or os.getuid() != os.geteuid() or os.getgid() != os.getegid():
        _fail('operator.identity_invalid')
    descriptors = (0, 1, 2)
    tty: int | None = None
    try:
        if not all(os.isatty(fd) for fd in descriptors):
            _fail('operator.tty_required')
        metadata = [os.fstat(fd) for fd in descriptors]
        identities = [(item.st_dev, item.st_rdev) for item in metadata]
        if len(set(identities)) != 1 or any(os.tcgetpgrp(fd) != os.getpgrp() for fd in descriptors):
            _fail('operator.tty_mismatch')
        read_write_tty = next(
            (
                fd
                for fd in descriptors
                if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDWR
            ),
            None,
        )
        if read_write_tty is None:
            _fail('operator.tty_access_invalid')
        # Duplicate the terminal we have already authenticated instead of
        # reopening /dev/tty. Sandboxed macOS terminals can deny that path
        # open even when all inherited descriptors are a valid foreground
        # TTY; duplicating its descriptor is also immune to path substitution.
        tty = os.dup(read_write_tty)
        os.set_inheritable(tty, False)
        metadata = os.fstat(tty)
        if not stat.S_ISCHR(metadata.st_mode) or (metadata.st_dev, metadata.st_rdev) != identities[0] or os.tcgetpgrp(tty) != os.getpgrp() or os.get_inheritable(tty):
            _fail('operator.tty_mismatch')
        return tty
    except ConsumerError:
        if tty is not None:
            try:
                os.close(tty)
            except OSError:
                pass
        raise
    except (OSError, ValueError):
        if tty is not None:
            try:
                os.close(tty)
            except OSError:
                pass
        _fail('operator.tty_required')


def _write_all(descriptor: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = os.write(descriptor, value[offset:])
        if written <= 0:
            _fail('operator.tty_failed')
        offset += written


def _read_prompt(descriptor: int, prompt: str, deadline: float) -> str:
    _write_all(descriptor, prompt.encode('ascii'))
    value = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _fail('operator.confirmation_timeout')
        ready, _, _ = select.select([descriptor], [], [], remaining)
        if not ready:
            _fail('operator.confirmation_timeout')
        chunk = os.read(descriptor, 1)
        if not chunk:
            _fail('operator.confirmation_eof')
        if chunk == b'\n':
            break
        if chunk in {b'\r', b'\x00'} or len(value) >= MAX_PROMPT_BYTES:
            _fail('operator.confirmation_invalid')
        value.extend(chunk)
    try:
        return value.decode('ascii')
    except UnicodeDecodeError:
        _fail('operator.confirmation_invalid')


def _confirm_operator(binding: LocalBinding, expected_source_commit: str, expected_saved_plan_sha256: str, expected_receipt_sha256: str, deadline: float, challenge_source: Callable[[int], bytes]=secrets.token_bytes) -> None:
    challenge_bytes = challenge_source(8)
    if type(challenge_bytes) is not bytes or len(challenge_bytes) != 8:
        _fail('operator.challenge_invalid')
    challenge = challenge_bytes.hex().upper()
    tty = _validate_operator_tty()
    expected = (
        expected_source_commit,
        expected_saved_plan_sha256,
        expected_receipt_sha256,
        'APPLY STAGING-REHEARSAL SERIAL 9: NO-OP 28, CREATE 11, UPDATE 0, DELETE 0, REPLACE 0',
        f'APPLY {challenge}',
    )
    prompts = (
        f'STAGING REHEARSAL ONLY. Type exact commit {expected_source_commit}: ',
        f'Type exact saved-plan SHA-256 {expected_saved_plan_sha256}: ',
        f'Type exact handoff-receipt SHA-256 {expected_receipt_sha256}: ',
        'Type the exact change statement: ',
        f'Type APPLY {challenge}: ',
    )
    try:
        for prompt, exact in zip(prompts, expected, strict=True):
            if _read_prompt(tty, prompt, deadline) != exact:
                _fail('operator.confirmation_mismatch')
        _require_deadline(deadline)
    finally:
        os.close(tty)


def _revalidate_operator_tty() -> None:
    tty = _validate_operator_tty()
    os.close(tty)


def _validate_live(binding: LocalBinding, *, execution_dir: Path, expected_principal_sha256: str, reader_factory: Callable[[], Any], clock: Callable[[], datetime]) -> tuple[Any, datetime, bytes]:
    receipt = binding.receipt
    expected_state = binding.state
    first_state = handoff._pull_remote_state(tofu=binding.tofu, data_dir=execution_dir, expected_state=expected_state, expected_backend_sha256=str(receipt['backend_metadata_sha256']), expected_tofu_sha256=str(receipt['open_tofu_sha256']))
    if first_state != receipt.get('remote_state_sha256'):
        _fail('remote_state.changed')
    attributes = handoff._state_attributes(_canonical(expected_state))
    reader = reader_factory()
    first = handoff._capture_snapshot(reader, attributes)
    reader.refresh_account()
    second = handoff._capture_snapshot(reader, attributes)
    if _canonical(first) != _canonical(second) or _digest(_canonical(first)) != receipt.get('live_snapshot_sha256'):
        _fail('live_snapshot.changed')
    account = first.get('account')
    if not isinstance(account, Mapping) or account.get('principal_sha256') != expected_principal_sha256:
        _fail('azure_principal.mismatch')
    if any(getattr(reader, name) != receipt.get(name) for name in ('azure_cli_sha256', 'azure_cli_version_sha256', 'azure_tls_ca_bundle_sha256', 'azure_config_binding_sha256')):
        _fail('azure_runtime.changed')
    reader.revalidate_runtime()
    second_state = handoff._pull_remote_state(tofu=binding.tofu, data_dir=execution_dir, expected_state=expected_state, expected_backend_sha256=str(receipt['backend_metadata_sha256']), expected_tofu_sha256=str(receipt['open_tofu_sha256']))
    if second_state != first_state or recovery._provider_tree_sha256(execution_dir) != receipt.get('execution_provider_tree_sha256'):
        _fail('runtime.changed')
    now = _utc_now(clock)
    if not reader.dates:
        _fail('live_snapshot.time_missing')
    oldest, newest = min(reader.dates), max(reader.dates)
    if newest - oldest > handoff.MAX_OBSERVATION_SPAN or newest > now + handoff.MAX_CLOCK_SKEW or now - newest > handoff.MAX_CLOCK_SKEW or newest < binding.observed or now >= binding.expires or newest >= binding.expires:
        _fail('live_snapshot.stale')
    return reader, newest, _canonical(first)


def _validate_action_time(binding: LocalBinding, *, newest_arm_date: datetime, clock: Callable[[], datetime]) -> datetime:
    now = _utc_now(clock)
    try:
        normalized_plan_time = recovery._validate_plan_timestamp({'timestamp': binding.base.get('plan_timestamp')}, now=now)
    except Exception:
        _fail('plan.timestamp_invalid')
    if normalized_plan_time != binding.base.get('plan_timestamp'):
        _fail('plan.timestamp_invalid')
    if newest_arm_date < binding.observed or newest_arm_date > now + handoff.MAX_CLOCK_SKEW or now - newest_arm_date > handoff.MAX_CLOCK_SKEW or now >= binding.expires or newest_arm_date >= binding.expires:
        _fail('live_snapshot.stale')
    return now


def _revalidate_preclaim(binding: LocalBinding, *, execution_dir: Path, reader: Any, expected_source_commit: str, expected_principal_sha256: str, expected_live_snapshot: bytes, deadline: float, clock: Callable[[], datetime]) -> datetime:
    receipt = binding.receipt
    _require_deadline(deadline)
    if reader.begin_action_readback(deadline=deadline) != deadline:
        _fail('live_snapshot.window_invalid')
    reader.refresh_account()
    account = reader.account
    if account.get('id') != handoff.SUBSCRIPTION or account.get('tenantId') != handoff.TENANT or account.get('environmentName') != 'AzureCloud' or account.get('state') != 'Enabled' or not isinstance(account.get('user'), Mapping) or account['user'].get('type') != 'user' or _digest(str(account['user'].get('name', '')).encode()) != expected_principal_sha256:
        _fail('azure_account.mismatch')
    if any(getattr(reader, name) != receipt.get(name) for name in ('azure_cli_sha256', 'azure_cli_version_sha256', 'azure_tls_ca_bundle_sha256', 'azure_config_binding_sha256')):
        _fail('azure_runtime.changed')
    date_start = len(reader.dates)
    final_snapshot = _canonical(handoff._capture_snapshot(reader, handoff._state_attributes(_canonical(binding.state))))
    if final_snapshot != expected_live_snapshot or _digest(final_snapshot) != receipt.get('live_snapshot_sha256'):
        _fail('live_snapshot.changed')
    final_dates = reader.dates[date_start:]
    if not final_dates or any(not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0) for value in final_dates) or max(final_dates) - min(final_dates) > handoff.MAX_OBSERVATION_SPAN:
        _fail('live_snapshot.time_missing')
    newest_arm_date = max(final_dates)
    reader.revalidate_runtime()
    _require_deadline(deadline)
    final_state = handoff._pull_remote_state(tofu=binding.tofu, data_dir=execution_dir, expected_state=binding.state, expected_backend_sha256=str(receipt['backend_metadata_sha256']), expected_tofu_sha256=str(receipt['open_tofu_sha256']))
    if final_state != receipt.get('remote_state_sha256'):
        _fail('remote_state.changed')
    _require_deadline(deadline)
    if recovery._provider_tree_sha256(execution_dir) != receipt.get('execution_provider_tree_sha256'):
        _fail('runtime.changed')
    _require_deadline(deadline)
    source, preparer_digest = _source_binding(expected_source_commit, str(binding.base.get('source_tree_sha256', '')))
    if source.tree_sha256 != binding.base.get('source_tree_sha256') or source.tree_sha256 != receipt.get('source_tree_sha256') or preparer_digest != receipt.get('handoff_source_sha256'):
        _fail('source.changed')
    _validate_action_time(binding, newest_arm_date=newest_arm_date, clock=clock)
    _require_deadline(deadline)
    return newest_arm_date


def _open_pinned_plan(saved_plan: Path, *, expected_sha256: str, expected_identity: tuple[int, int, int, int, int, int, int]) -> int:
    parent = recovery._open_protected_directory(saved_plan.parent, label='saved plan directory')
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(saved_plan.name, flags, dir_fd=parent)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o400 or not 0 < before.st_size <= recovery.MAXIMUM_ARTIFACT_BYTES or _metadata_identity(before) != expected_identity:
            _fail('saved_plan.changed')
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if remaining or _metadata_identity(after) != expected_identity or digest.hexdigest() != expected_sha256 or os.lseek(descriptor, 0, os.SEEK_SET) != 0:
            _fail('saved_plan.changed')
        os.set_inheritable(descriptor, True)
        if not os.get_inheritable(descriptor):
            _fail('saved_plan.unavailable')
        return descriptor
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise
    finally:
        os.close(parent)


def _execution_arguments(tofu: Path, plan_descriptor: int) -> list[str]:
    return [str(tofu), f'-chdir={recovery.REHEARSAL_ROOT}', 'apply', '-input=false', '-lock=true', '-lock-timeout=0s', '-no-color', f'/dev/fd/{plan_descriptor}']


def _execution_environment(*, azure_cli: Path, home: Path, azure_config: Path, execution_dir: Path) -> dict[str, str]:
    path = ':'.join(dict.fromkeys((str(azure_cli.parent), '/usr/bin', '/bin')))
    return {
        'HOME': str(home), 'PATH': path, 'LC_ALL': 'C', 'LANG': 'C',
        'AZURE_CONFIG_DIR': str(azure_config), 'AZURE_CORE_ONLY_SHOW_ERRORS': 'true',
        'AZURE_CORE_COLLECT_TELEMETRY': 'false',
        'AZURE_CORE_DISABLE_CONFIRM_PROMPT': 'true',
        'AZURE_EXTENSION_USE_DYNAMIC_INSTALL': 'no',
        'CHECKPOINT_DISABLE': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_NO_REPLACE_OBJECTS': '1',
        'GIT_TERMINAL_PROMPT': '0', 'TF_CLI_CONFIG_FILE': os.devnull,
        'TF_DATA_DIR': str(execution_dir), 'TF_IN_AUTOMATION': '1', 'TF_INPUT': '0',
        'ARM_SUBSCRIPTION_ID': recovery.EXPECTED_SUBSCRIPTION_ID,
        'ARM_TENANT_ID': recovery.EXPECTED_TENANT_ID, 'ARM_ENVIRONMENT': 'public',
        'ARM_USE_CLI': 'true', 'ARM_USE_AZUREAD': 'true',
    }


def _claim(execution_dir: Path, facts: Mapping[str, Any]) -> None:
    payload = _canonical(facts) + b'\n'
    if len(payload) > MAX_RECEIPT_BYTES:
        _fail('claim.invalid')
    parent = recovery._open_protected_directory(execution_dir, label='execution directory')
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(CLAIM_NAME, flags, 0o400, dir_fd=parent)
        _write_all(descriptor, payload)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != 0o400 or metadata.st_size != len(payload):
            _fail('claim.invalid')
        os.fsync(parent)
    except OSError:
        _fail('claim.exists_or_unavailable')
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _reject_ambient_environment() -> None:
    handoff._reject_ambient_transport_environment()
    forbidden_exact = {'LD_PRELOAD', 'LD_LIBRARY_PATH', 'DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH'}
    forbidden_prefixes = ('PYTHON', 'TF_', 'TOFU_', 'OPENTOFU_', 'GIT_', 'ARM_', 'AZURE_')
    if any(name in forbidden_exact or name.startswith(forbidden_prefixes) for name in os.environ):
        _fail('environment.unsafe')


def consume_handoff(*, saved_plan: Path, state_json: Path, execution_dir: Path, receipt_path: Path, expected_source_commit: str, expected_saved_plan_sha256: str, expected_receipt_sha256: str, expected_azure_principal_sha256: str, reader_factory: Callable[[], Any]=handoff.AzureManagementReader, clock: Callable[[], datetime]=lambda: datetime.now(UTC), boot_session: Callable[[], bytes]=handoff._boot_session, challenge_source: Callable[[int], bytes]=secrets.token_bytes) -> NoReturn:
    if COMMIT_RE.fullmatch(expected_source_commit) is None or any(HASH_RE.fullmatch(value) is None for value in (expected_saved_plan_sha256, expected_receipt_sha256, expected_azure_principal_sha256)):
        _fail('expected_binding.invalid')
    _reject_ambient_environment()
    python_binding = handoff._python_runtime_binding()
    azure_cli = handoff._resolve_azure_cli()
    home, azure_config, _ = handoff._operator_paths()
    with handoff._pinned_process_environment(azure_cli, home):
        initial = _validate_local(saved_plan=saved_plan, state_json=state_json, execution_dir=execution_dir, receipt_path=receipt_path, expected_source_commit=expected_source_commit, expected_saved_plan_sha256=expected_saved_plan_sha256, expected_receipt_sha256=expected_receipt_sha256, python_binding=python_binding, clock=clock, boot_session=boot_session)
        reader, _, live_snapshot = _validate_live(initial, execution_dir=execution_dir, expected_principal_sha256=expected_azure_principal_sha256, reader_factory=reader_factory, clock=clock)
        final = _validate_local(saved_plan=saved_plan, state_json=state_json, execution_dir=execution_dir, receipt_path=receipt_path, expected_source_commit=expected_source_commit, expected_saved_plan_sha256=expected_saved_plan_sha256, expected_receipt_sha256=expected_receipt_sha256, python_binding=python_binding, clock=clock, boot_session=boot_session)
        if initial != final:
            _fail('binding.changed')
        deadline = time.monotonic() + CONFIRMATION_SECONDS
        _confirm_operator(final, expected_source_commit, expected_saved_plan_sha256, expected_receipt_sha256, deadline, challenge_source)
        newest_arm_date = _revalidate_preclaim(final, execution_dir=execution_dir, reader=reader, expected_source_commit=expected_source_commit, expected_principal_sha256=expected_azure_principal_sha256, expected_live_snapshot=live_snapshot, deadline=deadline, clock=clock)
        environment = _execution_environment(azure_cli=final.tofu.with_name('az'), home=home, azure_config=azure_config, execution_dir=execution_dir)
        plan_descriptor: int | None = None
        try:
            plan_descriptor = _open_pinned_plan(saved_plan, expected_sha256=expected_saved_plan_sha256, expected_identity=final.identities[0])
            _require_deadline(deadline)
            _revalidate_operator_tty()
            claim_time = _validate_action_time(final, newest_arm_date=newest_arm_date, clock=clock)
            argv = _execution_arguments(final.tofu, plan_descriptor)
            claim = {
                'schema_version': 1, 'policy_id': 'cosmaneura-staging-rehearsal-base-recovery-apply-claim-v1',
                'source_commit': expected_source_commit, 'saved_plan_sha256': expected_saved_plan_sha256,
                'handoff_receipt_sha256': expected_receipt_sha256,
                'nonce_sha256': final.receipt['nonce_sha256'],
                'machine_binding_sha256': final.receipt['machine_binding_sha256'],
                'argv_sha256': _digest(_canonical(argv)), 'environment_sha256': _digest(_canonical(environment)),
                'claimed_at': claim_time.isoformat().replace('+00:00', 'Z'),
                'apply_outcome': 'unknown',
            }
            _require_deadline(deadline)
            _claim(execution_dir, claim)
            _require_deadline(deadline)
            os.execve(str(final.tofu), argv, environment)
            _fail('execve.returned')
        except BaseException:
            if plan_descriptor is not None:
                try:
                    os.close(plan_descriptor)
                except OSError:
                    pass
            raise


class _RefusingParser(argparse.ArgumentParser):

    def error(self, message: str) -> NoReturn:
        del message
        _fail('arguments.invalid')


def _parser() -> argparse.ArgumentParser:
    parser = _RefusingParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--saved-plan', required=True, type=Path)
    parser.add_argument('--state-json', required=True, type=Path)
    parser.add_argument('--execution-dir', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    parser.add_argument('--expected-source-commit', required=True)
    parser.add_argument('--expected-saved-plan-sha256', required=True)
    parser.add_argument('--expected-receipt-sha256', required=True)
    parser.add_argument('--expected-azure-principal-sha256', required=True)
    return parser


def main(argv: Sequence[str] | None=None) -> int:
    try:
        args = _parser().parse_args(argv)
        consume_handoff(saved_plan=args.saved_plan, state_json=args.state_json, execution_dir=args.execution_dir, receipt_path=args.receipt, expected_source_commit=args.expected_source_commit, expected_saved_plan_sha256=args.expected_saved_plan_sha256, expected_receipt_sha256=args.expected_receipt_sha256, expected_azure_principal_sha256=args.expected_azure_principal_sha256)
    except (Exception, KeyboardInterrupt):
        print('recovery_apply_refused', file=sys.stderr)
        return 2
    return 2


if __name__ == '__main__':
    raise SystemExit(main())

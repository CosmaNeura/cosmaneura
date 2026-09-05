#!/usr/bin/env python3
# ruff: noqa: I001
# fmt: off
from __future__ import annotations
import ast
import hashlib
import json
import os
import stat
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
import pytest
from scripts import consume_staging_rehearsal_base_recovery_handoff as consumer

NOW = datetime(2026, 9, 5, 4, 5, tzinfo=UTC)
OBSERVED = NOW - timedelta(minutes=5)
SOURCE = '1' * 40
PLAN_DIGEST = '2' * 64
RECEIPT_DIGEST = '3' * 64
PRINCIPAL_DIGEST = hashlib.sha256(b'operator@example.invalid').hexdigest()


def _base() -> dict[str, Any]:
    return {
        'schema_version': 2,
        'policy_id': 'cosmaneura-staging-rehearsal-base-recovery-plan-v2',
        'generation_mode': 'digest_bound_revalidation',
        'source_commit': SOURCE,
        'source_tree_sha256': '4' * 64,
        'provider_lock_sha256': '5' * 64,
        'backend_metadata_sha256': '6' * 64,
        'backend_workspace': 'default',
        'state_lineage': consumer.recovery.EXPECTED_LINEAGE,
        'state_serial': 8,
        'state_json_sha256': '7' * 64,
        'embedded_state_sha256': '8' * 64,
        'saved_plan_sha256': PLAN_DIGEST,
        'derived_plan_json_sha256': '9' * 64,
        'plan_timestamp': '2026-09-05T04:00:00Z',
        'open_tofu_version': '1.12.5',
        'validated_nonempty_change_set': True,
        'artifact_policy_complete': True,
        'live_readback_bound': False,
        'apply_authorized': False,
        'full_refresh_generated_here': False,
        'existing_resource_count': 28,
        'create_count': 11,
        'update_count': 0,
        'destroy_or_replace_count': 0,
        'pass': True,
        'execution_data_dir': '/protected/execution',
        'execution_provider_tree_sha256': 'a' * 64,
        'open_tofu_path': '/trusted/tofu',
        'open_tofu_sha256': 'b' * 64,
    }


def _receipt(base: Mapping[str, Any] | None=None) -> dict[str, Any]:
    base = dict(base or _base())
    return {
        'schema_version': 1,
        'policy_id': consumer.POLICY_ID,
        'source_commit': SOURCE,
        'source_tree_sha256': base['source_tree_sha256'],
        'handoff_source_sha256': 'c' * 64,
        'provider_lock_sha256': base['provider_lock_sha256'],
        'state_lineage': consumer.recovery.EXPECTED_LINEAGE,
        'state_serial': 8,
        'state_json_sha256': base['state_json_sha256'],
        'remote_state_sha256': 'd' * 64,
        'saved_plan_sha256': PLAN_DIGEST,
        'derived_plan_json_sha256': base['derived_plan_json_sha256'],
        'backend_metadata_sha256': base['backend_metadata_sha256'],
        'open_tofu_sha256': base['open_tofu_sha256'],
        'execution_provider_tree_sha256': base['execution_provider_tree_sha256'],
        'python_executable_sha256': 'e' * 64,
        'python_runtime_sha256': 'f' * 64,
        'azure_cli_sha256': '0' * 64,
        'azure_cli_version_sha256': '1' * 64,
        'azure_tls_ca_bundle_sha256': '2' * 64,
        'azure_config_binding_sha256': '3' * 64,
        'base_validation_receipt_sha256': consumer._digest(consumer._canonical(base)),
        'machine_binding_sha256': '4' * 64,
        'nonce_sha256': '5' * 64,
        'live_snapshot_sha256': '6' * 64,
        'observed_at': OBSERVED.isoformat().replace('+00:00', 'Z'),
        'expires_at': (OBSERVED + timedelta(minutes=10)).isoformat().replace('+00:00', 'Z'),
        'existing_azure_resource_count': 26,
        'planned_absent_resource_count': 11,
        'target_resource_count': 0,
        'live_readback_bound': True,
        'apply_authorized': False,
        'pass': True,
    }


def _python_binding() -> dict[str, str]:
    return {'python_executable_sha256': 'e' * 64, 'python_runtime_sha256': 'f' * 64}


def _validate_receipt(receipt: Mapping[str, Any], base: Mapping[str, Any] | None=None, *, now: datetime=NOW) -> tuple[datetime, datetime]:
    return consumer._validate_receipt(
        receipt, dict(base or _base()), expected_source_commit=SOURCE,
        expected_saved_plan_sha256=PLAN_DIGEST, handoff_source_sha256='c' * 64,
        python_binding=_python_binding(), now=now,
    )


def _artifacts(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    tmp_path.chmod(0o700)
    plan, state, receipt = tmp_path / 'plan', tmp_path / 'state', tmp_path / 'receipt'
    execution = tmp_path / 'execution'
    execution.mkdir(mode=0o700)
    for path, value, mode in ((plan, b'plan', 0o400), (state, b'{}', 0o600), (receipt, b'{}\n', 0o400), (execution / consumer.NONCE_NAME, b'n' * 32, 0o400)):
        path.write_bytes(value)
        path.chmod(mode)
    return plan, state, execution, receipt


def _binding(execution: Path, receipt: Mapping[str, Any] | None=None) -> consumer.LocalBinding:
    value = dict(receipt or _receipt())
    return consumer.LocalBinding(
        value, consumer._canonical(value) + b'\n', _base(), Path('/trusted/tofu'),
        {'serial': 8}, b'n' * 32, OBSERVED, OBSERVED + timedelta(minutes=10),
        ((1, 1, os.geteuid(), 0o400, 1, 1),),
    )


def test_contract_and_cli_expose_no_bypass_or_extra_apply_argument() -> None:
    actions = {action.dest for action in consumer._parser()._actions}
    assert actions == {
        'help', 'saved_plan', 'state_json', 'execution_dir', 'receipt',
        'expected_source_commit', 'expected_saved_plan_sha256',
        'expected_receipt_sha256', 'expected_azure_principal_sha256',
    }
    assert len(consumer.RECEIPT_FIELDS) == 33
    assert Path(consumer.__file__).read_text().splitlines()[0] == '#!/opt/homebrew/bin/python3 -IS'
    tree = ast.parse(Path(consumer.__file__).read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'os' and node.func.attr == 'execve']
    assert len(calls) == 1 and len(calls[0].args) == 3 and not calls[0].keywords


def test_receipt_reader_requires_external_digest_canonical_newline_and_no_duplicate_keys(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    path = tmp_path / 'receipt'
    raw = consumer._canonical(_receipt()) + b'\n'
    path.write_bytes(raw)
    path.chmod(0o400)
    assert consumer._read_receipt(path, consumer._digest(raw))[0] == _receipt()
    for malformed in (
        raw[:-1],
        b'{"a":1,"a":1}\n',
        b'{"a":NaN}\n',
        b'[' + b'0' * consumer.MAX_RECEIPT_BYTES + b']\n',
    ):
        path.chmod(0o600)
        path.write_bytes(malformed)
        path.chmod(0o400)
        with pytest.raises(consumer.ConsumerError):
            consumer._read_receipt(path, consumer._digest(malformed))
    path.chmod(0o600)
    path.write_bytes(raw)
    path.chmod(0o400)
    with pytest.raises(consumer.ConsumerError, match='digest'):
        consumer._read_receipt(path, '0' * 64)


def test_oversized_receipt_is_rejected_from_metadata_before_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path.chmod(0o700)
    path = tmp_path / 'receipt'
    path.write_bytes(b'x' * (consumer.MAX_RECEIPT_BYTES + 1))
    path.chmod(0o400)
    reads: list[int] = []
    monkeypatch.setattr(consumer.os, 'read', lambda *_: reads.append(1) or b'')
    with pytest.raises(consumer.ConsumerError, match='receipt.unsafe'):
        consumer._read_receipt(path, '0' * 64)
    assert reads == []


def test_local_validation_rejects_oversized_receipt_before_generic_or_custom_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, state, execution, receipt = _artifacts(tmp_path)
    receipt.chmod(0o600)
    receipt.write_bytes(b'x' * (consumer.MAX_RECEIPT_BYTES + 1))
    receipt.chmod(0o400)
    labels: list[str] = []
    original = consumer.recovery._read_protected_file
    def tracked(path: Path, *, label: str, required_mode: int | None=None) -> bytes:
        labels.append(label)
        return original(path, label=label, required_mode=required_mode)
    monkeypatch.setattr(consumer.recovery, '_read_protected_file', tracked)
    with pytest.raises(consumer.ConsumerError, match='receipt.unsafe'):
        consumer._validate_local(saved_plan=plan, state_json=state, execution_dir=execution, receipt_path=receipt, expected_source_commit=SOURCE, expected_saved_plan_sha256=PLAN_DIGEST, expected_receipt_sha256='0' * 64, python_binding=_python_binding(), clock=lambda: NOW, boot_session=lambda: b'boot')
    assert 'handoff receipt' not in labels


@pytest.mark.parametrize('field,value', [
    ('schema_version', True), ('policy_id', 'wrong'), ('source_commit', 'a' * 40),
    ('state_serial', True), ('state_lineage', 'wrong'), ('saved_plan_sha256', '0' * 64),
    ('existing_azure_resource_count', 27), ('planned_absent_resource_count', 10),
    ('target_resource_count', 1), ('live_readback_bound', 1),
    ('apply_authorized', True), ('pass', 1), ('python_runtime_sha256', 'x' * 64),
    ('base_validation_receipt_sha256', '0' * 64),
])
def test_receipt_schema_and_every_authorizing_fact_fail_closed(field: str, value: Any) -> None:
    receipt = _receipt()
    receipt[field] = value
    with pytest.raises(consumer.ConsumerError):
        _validate_receipt(receipt)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'bad_hash', 'bad_observed', 'bad_window', 'expired', 'future'])
def test_receipt_shape_hashes_and_expiry_fail_closed(mutation: str) -> None:
    receipt = _receipt()
    now = NOW
    if mutation == 'missing':
        receipt.pop('remote_state_sha256')
    elif mutation == 'extra':
        receipt['extra'] = True
    elif mutation == 'bad_hash':
        receipt['remote_state_sha256'] = 'A' * 64
    elif mutation == 'bad_observed':
        receipt['observed_at'] = '2026-09-05 04:00:00Z'
    elif mutation == 'bad_window':
        receipt['expires_at'] = (OBSERVED + timedelta(minutes=9)).isoformat().replace('+00:00', 'Z')
    elif mutation == 'expired':
        now = OBSERVED + timedelta(minutes=10)
    else:
        now = OBSERVED - consumer.handoff.MAX_CLOCK_SKEW - timedelta(seconds=1)
    with pytest.raises(consumer.ConsumerError):
        _validate_receipt(receipt, now=now)


@pytest.mark.parametrize('field,value', [
    ('existing_resource_count', 27), ('create_count', 10), ('update_count', 1),
    ('destroy_or_replace_count', 1), ('live_readback_bound', True),
    ('apply_authorized', True), ('pass', False), ('state_serial', 7),
    ('state_lineage', 'wrong'), ('backend_workspace', 'other'),
    ('open_tofu_version', '1.13.0'), ('generation_mode', 'validator_full_refresh'),
])
def test_reconstructed_plan_requires_exact_28_noops_11_creates(field: str, value: Any) -> None:
    base = _base()
    base[field] = value
    receipt = _receipt(base)
    with pytest.raises(consumer.ConsumerError):
        _validate_receipt(receipt, base)


def test_input_paths_require_same_protected_parent_exact_modes_and_regular_single_links(tmp_path: Path) -> None:
    plan, state, execution, receipt = _artifacts(tmp_path)
    consumer._validate_paths(plan, state, execution, receipt)
    cases = ((plan, 0o600), (state, 0o400), (receipt, 0o600), (execution, 0o755), (execution / consumer.NONCE_NAME, 0o600))
    for path, mode in cases:
        original = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode)
        with pytest.raises(Exception):
            consumer._validate_paths(plan, state, execution, receipt)
        path.chmod(original)
    outside = tmp_path.parent / f'{tmp_path.name}-outside-state'
    outside.write_bytes(b'{}')
    outside.chmod(0o600)
    try:
        with pytest.raises(consumer.ConsumerError, match='parent'):
            consumer._validate_paths(plan, outside, execution, receipt)
    finally:
        outside.unlink()
    hardlink = tmp_path / 'plan-link'
    os.link(plan, hardlink)
    with pytest.raises(Exception):
        consumer._validate_paths(plan, state, execution, receipt)


def test_input_paths_reject_relative_symlink_and_fifo(tmp_path: Path) -> None:
    plan, state, execution, receipt = _artifacts(tmp_path)
    with pytest.raises(consumer.ConsumerError, match='path.invalid'):
        consumer._validate_paths(Path('plan'), state, execution, receipt)
    target = tmp_path / 'target'
    target.write_bytes(b'x')
    target.chmod(0o400)
    plan.unlink()
    plan.symlink_to(target)
    with pytest.raises(Exception):
        consumer._validate_paths(plan, state, execution, receipt)
    plan.unlink()
    os.mkfifo(plan, 0o400)
    with pytest.raises(Exception):
        consumer._validate_paths(plan, state, execution, receipt)


def test_local_binding_reconstructs_receipt_and_machine_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, state, execution, receipt_path = _artifacts(tmp_path)
    base = _base()
    base['execution_data_dir'] = str(execution)
    base['state_json_sha256'] = consumer._digest(state.read_bytes())
    receipt = _receipt(base)
    nonce = (execution / consumer.NONCE_NAME).read_bytes()
    receipt['nonce_sha256'] = consumer._digest(nonce)
    receipt['machine_binding_sha256'] = consumer._machine_binding(execution, nonce, b'boot')
    raw = consumer._canonical(receipt) + b'\n'
    receipt_path.chmod(0o600)
    receipt_path.write_bytes(raw)
    receipt_path.chmod(0o400)
    monkeypatch.setattr(consumer, '_base_receipt', lambda **_: (base, Path('/trusted/tofu')))
    snapshot = consumer.recovery.SourceSnapshot(SOURCE, {}, b'lock', '4' * 64)
    monkeypatch.setattr(consumer, '_source_binding', lambda *_: (snapshot, 'c' * 64))
    monkeypatch.setattr(consumer.handoff, '_python_runtime_binding', _python_binding)
    monkeypatch.setattr(consumer.recovery, '_validate_state', lambda *_args, **_kwargs: (consumer.recovery.EXPECTED_LINEAGE, 8, {}))
    binding = consumer._validate_local(saved_plan=plan, state_json=state, execution_dir=execution, receipt_path=receipt_path, expected_source_commit=SOURCE, expected_saved_plan_sha256=PLAN_DIGEST, expected_receipt_sha256=consumer._digest(raw), python_binding=_python_binding(), clock=lambda: NOW, boot_session=lambda: b'boot')
    assert binding.receipt == receipt and binding.nonce == nonce and binding.tofu == Path('/trusted/tofu')
    with pytest.raises(consumer.ConsumerError, match='machine_binding'):
        consumer._validate_local(saved_plan=plan, state_json=state, execution_dir=execution, receipt_path=receipt_path, expected_source_commit=SOURCE, expected_saved_plan_sha256=PLAN_DIGEST, expected_receipt_sha256=consumer._digest(raw), python_binding=_python_binding(), clock=lambda: NOW, boot_session=lambda: b'different-boot')


def test_local_binding_rejects_semantically_equal_state_swapped_between_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, state, execution, receipt_path = _artifacts(tmp_path)
    original_state = state.read_bytes()
    base = _base()
    base['execution_data_dir'] = str(execution)
    base['state_json_sha256'] = consumer._digest(original_state)
    receipt = _receipt(base)
    nonce = (execution / consumer.NONCE_NAME).read_bytes()
    receipt['nonce_sha256'] = consumer._digest(nonce)
    receipt['machine_binding_sha256'] = consumer._machine_binding(execution, nonce, b'boot')
    raw = consumer._canonical(receipt) + b'\n'
    receipt_path.chmod(0o600)
    receipt_path.write_bytes(raw)
    receipt_path.chmod(0o400)
    def swap_after_base_validation(**_: Any) -> tuple[Mapping[str, Any], Path]:
        state.write_bytes(b'{ }')
        return base, Path('/trusted/tofu')
    monkeypatch.setattr(consumer, '_base_receipt', swap_after_base_validation)
    snapshot = consumer.recovery.SourceSnapshot(SOURCE, {}, b'lock', '4' * 64)
    monkeypatch.setattr(consumer, '_source_binding', lambda *_: (snapshot, 'c' * 64))
    monkeypatch.setattr(consumer.handoff, '_python_runtime_binding', _python_binding)
    with pytest.raises(consumer.ConsumerError, match='state_snapshot.changed'):
        consumer._validate_local(saved_plan=plan, state_json=state, execution_dir=execution, receipt_path=receipt_path, expected_source_commit=SOURCE, expected_saved_plan_sha256=PLAN_DIGEST, expected_receipt_sha256=consumer._digest(raw), python_binding=_python_binding(), clock=lambda: NOW, boot_session=lambda: b'boot')
    assert json.loads(original_state) == json.loads(state.read_bytes())


@pytest.mark.parametrize('value', [datetime(2026, 9, 5, 4, 5), datetime(2026, 9, 5, 0, 5, tzinfo=timezone(timedelta(hours=-4)))])
def test_clock_must_be_explicit_utc(value: datetime) -> None:
    with pytest.raises(consumer.ConsumerError, match='clock.invalid'):
        consumer._utc_now(lambda: value)


def test_source_binding_requires_head_tree_and_committed_validator_preparer_consumer(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = consumer.recovery.SourceSnapshot(SOURCE, {}, b'lock', '4' * 64)
    monkeypatch.setattr(consumer.recovery, '_source_snapshot', lambda _: snapshot)
    expected: dict[str, bytes] = {}
    for path in consumer.SOURCE_PATHS[1:]:
        expected[str(path.relative_to(consumer.recovery.REPOSITORY_ROOT))] = path.read_bytes()
    dirty = False
    def git(arguments: list[str]) -> bytes:
        if arguments[0] == 'status':
            return b' M scripts/x.py\n' if dirty else b''
        return expected[arguments[1].split(':', 1)[1]]
    monkeypatch.setattr(consumer.recovery, '_run_git', git)
    _, preparer = consumer._source_binding(SOURCE, '4' * 64)
    assert preparer == consumer._digest(consumer._HANDOFF_FILE.read_bytes())
    with pytest.raises(consumer.ConsumerError):
        consumer._source_binding(SOURCE, '0' * 64)
    expected[str(consumer.SOURCE_PATHS[-1].relative_to(consumer.recovery.REPOSITORY_ROOT))] = b'changed'
    with pytest.raises(consumer.ConsumerError):
        consumer._source_binding(SOURCE, '4' * 64)


class _Reader:
    azure_cli_sha256 = '0' * 64
    azure_cli_version_sha256 = '1' * 64
    azure_tls_ca_bundle_sha256 = '2' * 64
    azure_config_binding_sha256 = '3' * 64
    def __init__(self) -> None:
        self.account: dict[str, Any] = {
            'id': consumer.handoff.SUBSCRIPTION,
            'tenantId': consumer.handoff.TENANT,
            'environmentName': 'AzureCloud',
            'state': 'Enabled',
            'user': {'type': 'user', 'name': 'operator@example.invalid'},
        }
        self.dates = [NOW]
        self.runtime_checked = False
    def refresh_account(self) -> None:
        pass
    def revalidate_runtime(self) -> None:
        self.runtime_checked = True


@pytest.mark.parametrize('failure', ['first_state', 'second_state', 'snapshot', 'principal', 'runtime', 'provider', 'no_date', 'stale', 'expired'])
def test_live_revalidation_is_closed_world_and_time_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    _, _, execution, _ = _artifacts(tmp_path)
    receipt = _receipt()
    reader = _Reader()
    projection = {'account': {'principal_sha256': PRINCIPAL_DIGEST}, 'exact': True}
    pulls = [receipt['remote_state_sha256'], receipt['remote_state_sha256']]
    if failure == 'first_state':
        pulls[0] = 'f' * 64
    elif failure == 'second_state':
        pulls[1] = 'f' * 64
    elif failure == 'principal':
        projection['account']['principal_sha256'] = 'f' * 64
    elif failure == 'runtime':
        reader.azure_cli_sha256 = 'f' * 64
    elif failure == 'no_date':
        reader.dates = []
    elif failure == 'stale':
        reader.dates = [NOW - timedelta(minutes=6)]
    elif failure == 'expired':
        reader.dates = [OBSERVED + timedelta(minutes=10)]
    snapshots = [projection, dict(projection)]
    if failure == 'snapshot':
        snapshots[1]['exact'] = False
    else:
        receipt['live_snapshot_sha256'] = consumer._digest(consumer._canonical(projection))
    monkeypatch.setattr(consumer.handoff, '_pull_remote_state', lambda **_: pulls.pop(0))
    monkeypatch.setattr(consumer.handoff, '_state_attributes', lambda _: {})
    monkeypatch.setattr(consumer.handoff, '_capture_snapshot', lambda *_: snapshots.pop(0))
    monkeypatch.setattr(consumer.recovery, '_provider_tree_sha256', lambda _: 'f' * 64 if failure == 'provider' else 'a' * 64)
    binding = _binding(execution, receipt)
    with pytest.raises(consumer.ConsumerError):
        consumer._validate_live(binding, execution_dir=execution, expected_principal_sha256=PRINCIPAL_DIGEST, reader_factory=lambda: reader, clock=lambda: NOW)


def test_live_revalidation_accepts_two_identical_snapshots_and_state_pulls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, execution, _ = _artifacts(tmp_path)
    receipt = _receipt()
    projection = {'account': {'principal_sha256': PRINCIPAL_DIGEST}, 'exact': True}
    receipt['live_snapshot_sha256'] = consumer._digest(consumer._canonical(projection))
    reader = _Reader()
    pulls: list[str] = []
    monkeypatch.setattr(consumer.handoff, '_pull_remote_state', lambda **_: pulls.append('pull') or receipt['remote_state_sha256'])
    monkeypatch.setattr(consumer.handoff, '_state_attributes', lambda _: {})
    monkeypatch.setattr(consumer.handoff, '_capture_snapshot', lambda *_: dict(projection))
    monkeypatch.setattr(consumer.recovery, '_provider_tree_sha256', lambda _: 'a' * 64)
    returned_reader, newest = consumer._validate_live(_binding(execution, receipt), execution_dir=execution, expected_principal_sha256=PRINCIPAL_DIGEST, reader_factory=lambda: reader, clock=lambda: NOW)
    assert returned_reader is reader and newest == NOW
    assert pulls == ['pull', 'pull'] and reader.runtime_checked


@pytest.mark.parametrize('failure', ['runtime', 'account', 'remote_state', 'stale', 'expired'])
def test_runtime_state_and_arm_freshness_are_rechecked_immediately_before_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    _, _, execution, _ = _artifacts(tmp_path)
    receipt = _receipt()
    binding = _binding(execution, receipt)
    reader = _Reader()
    now = NOW
    newest = NOW
    expires = NOW + timedelta(minutes=1)
    if failure == 'runtime':
        reader.azure_cli_sha256 = 'f' * 64
    elif failure == 'account':
        reader.account['user']['name'] = 'different@example.invalid'
    elif failure == 'stale':
        now = NOW + timedelta(minutes=6)
    elif failure == 'expired':
        expires = NOW
    binding = consumer.LocalBinding(binding.receipt, binding.receipt_bytes, binding.base, binding.tofu, binding.state, binding.nonce, binding.observed, expires, binding.identities)
    monkeypatch.setattr(consumer.handoff, '_pull_remote_state', lambda **_: 'f' * 64 if failure == 'remote_state' else receipt['remote_state_sha256'])
    with pytest.raises(consumer.ConsumerError):
        consumer._revalidate_preclaim(binding, execution_dir=execution, reader=reader, expected_principal_sha256=PRINCIPAL_DIGEST, newest_arm_date=newest, clock=lambda: now)


def test_prompt_reader_rejects_whitespace_eof_timeout_and_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[bytes] = []
    monkeypatch.setattr(consumer, '_write_all', lambda _fd, value: writes.append(value))
    monkeypatch.setattr(consumer.select, 'select', lambda *_: ([7], [], []))
    queue = list(b'exact\n')
    monkeypatch.setattr(consumer.os, 'read', lambda *_: bytes([queue.pop(0)]) if queue else b'')
    assert consumer._read_prompt(7, 'prompt: ', consumer.time.monotonic() + 10) == 'exact'
    for raw in (b'exact \n', b'\r', b'', b'x' * 257 + b'\n'):
        queue[:] = raw
        with pytest.raises(consumer.ConsumerError):
            value = consumer._read_prompt(7, 'prompt: ', consumer.time.monotonic() + 10)
            if value != 'exact':
                consumer._fail('operator.confirmation_mismatch')
    monkeypatch.setattr(consumer.select, 'select', lambda *_: ([], [], []))
    with pytest.raises(consumer.ConsumerError, match='timeout'):
        consumer._read_prompt(7, 'prompt: ', consumer.time.monotonic() + 10)


@pytest.mark.parametrize('case', ['root', 'non_tty', 'background', 'different'])
def test_operator_tty_rejects_root_non_tty_background_or_mixed_devices(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    monkeypatch.setattr(consumer.os, 'getuid', lambda: 0 if case == 'root' else 501)
    monkeypatch.setattr(consumer.os, 'geteuid', lambda: 501)
    monkeypatch.setattr(consumer.os, 'getgid', lambda: 20)
    monkeypatch.setattr(consumer.os, 'getegid', lambda: 20)
    monkeypatch.setattr(consumer.os, 'isatty', lambda fd: not (case == 'non_tty' and fd == 1))
    class S:
        st_dev = 1
        st_rdev = 2
        st_mode = stat.S_IFCHR
    monkeypatch.setattr(consumer.os, 'fstat', lambda fd: type('S', (), {'st_dev': 9 if case == 'different' and fd == 2 else 1, 'st_rdev': 2, 'st_mode': stat.S_IFCHR})())
    monkeypatch.setattr(consumer.os, 'tcgetpgrp', lambda _fd: 999 if case == 'background' else 7)
    monkeypatch.setattr(consumer.os, 'getpgrp', lambda: 7)
    monkeypatch.setattr(consumer.os, 'open', lambda *_args, **_kwargs: 8)
    monkeypatch.setattr(consumer.os, 'close', lambda _fd: None)
    with pytest.raises(consumer.ConsumerError):
        consumer._validate_operator_tty()


def test_confirm_requires_all_five_exact_values_under_one_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, execution, _ = _artifacts(tmp_path)
    answers: list[str] = []
    monkeypatch.setattr(consumer, '_validate_operator_tty', lambda: 7)
    monkeypatch.setattr(consumer.os, 'close', lambda fd: None)
    monkeypatch.setattr(consumer, '_read_prompt', lambda _fd, prompt, _deadline: answers.append(prompt) or ({
        0: SOURCE, 1: PLAN_DIGEST, 2: RECEIPT_DIGEST,
        3: 'APPLY STAGING-REHEARSAL SERIAL 8: CREATE 11, UPDATE 0, DELETE 0, REPLACE 0',
        4: 'APPLY 0001020304050607',
    }[len(answers) - 1]))
    consumer._confirm_operator(_binding(execution), SOURCE, PLAN_DIGEST, RECEIPT_DIGEST, lambda size: bytes(range(size)))
    assert len(answers) == 5 and all('exact' in value.lower() or 'APPLY' in value for value in answers)


def test_preclaim_tty_recheck_refuses_a_changed_foreground_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(consumer, '_validate_operator_tty', lambda: consumer._fail('operator.tty_mismatch'))
    with pytest.raises(consumer.ConsumerError, match='tty_mismatch'):
        consumer._revalidate_operator_tty()


def test_exact_execve_environment_and_one_use_claim_survive_exec_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, execution, _ = _artifacts(tmp_path)
    binding = _binding(execution)
    calls: list[tuple[str, list[str], dict[str, str]]] = []
    monkeypatch.setattr(consumer, '_reject_ambient_environment', lambda: None)
    monkeypatch.setattr(consumer.handoff, '_python_runtime_binding', _python_binding)
    monkeypatch.setattr(consumer.handoff, '_resolve_azure_cli', lambda: Path('/trusted/bin/az'))
    monkeypatch.setattr(consumer.handoff, '_operator_paths', lambda: (Path('/operator'), Path('/operator/.azure'), 'x' * 64))
    monkeypatch.setattr(consumer.handoff, '_pinned_process_environment', lambda *_: nullcontext())
    local_calls: list[str] = []
    monkeypatch.setattr(consumer, '_validate_local', lambda **_: local_calls.append('local') or binding)
    monkeypatch.setattr(consumer, '_confirm_operator', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(consumer, '_revalidate_operator_tty', lambda: None)
    reader = _Reader()
    monkeypatch.setattr(consumer, '_validate_live', lambda *_args, **_kwargs: (reader, NOW))
    monkeypatch.setattr(consumer.handoff, '_pull_remote_state', lambda **_: binding.receipt['remote_state_sha256'])
    def execve(path: str, argv: list[str], environment: dict[str, str]) -> None:
        calls.append((path, argv, environment))
        raise OSError('simulated exec failure')
    monkeypatch.setattr(consumer.os, 'execve', execve)
    with pytest.raises(OSError, match='simulated'):
        consumer.consume_handoff(saved_plan=tmp_path / 'plan', state_json=tmp_path / 'state', execution_dir=execution, receipt_path=tmp_path / 'receipt', expected_source_commit=SOURCE, expected_saved_plan_sha256=PLAN_DIGEST, expected_receipt_sha256=RECEIPT_DIGEST, expected_azure_principal_sha256=PRINCIPAL_DIGEST, clock=lambda: NOW)
    assert local_calls == ['local', 'local']
    path, argv, environment = calls[0]
    assert path == '/trusted/tofu'
    assert argv == ['/trusted/tofu', f'-chdir={consumer.recovery.REHEARSAL_ROOT}', 'apply', '-input=false', '-lock=true', '-lock-timeout=0s', '-no-color', str(tmp_path / 'plan')]
    assert environment == consumer._execution_environment(azure_cli=Path('/trusted/bin/az'), home=Path('/operator'), azure_config=Path('/operator/.azure'), execution_dir=execution)
    marker = execution / consumer.CLAIM_NAME
    claim = json.loads(marker.read_bytes())
    assert marker.stat().st_mode & 0o777 == 0o400 and claim['apply_outcome'] == 'unknown'
    assert claim['argv_sha256'] == consumer._digest(consumer._canonical(argv))
    with pytest.raises(consumer.ConsumerError, match='exists'):
        consumer._claim(execution, claim)
    assert marker.exists()


def test_ambient_override_families_and_bad_expected_bindings_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ('PYTHONPATH', 'LD_PRELOAD', 'HTTPS_PROXY', 'TF_VAR_x', 'TOFU_CONFIG', 'OPENTOFU_X', 'GIT_CONFIG', 'ARM_CLIENT_ID', 'AZURE_CONFIG_DIR'):
        monkeypatch.setenv(name, 'unsafe')
        with pytest.raises(Exception):
            consumer._reject_ambient_environment()
        monkeypatch.delenv(name)
    with pytest.raises(consumer.ConsumerError, match='expected_binding'):
        consumer.consume_handoff(saved_plan=Path('/p/plan'), state_json=Path('/p/state'), execution_dir=Path('/p/execution'), receipt_path=Path('/p/receipt'), expected_source_commit='short', expected_saved_plan_sha256=PLAN_DIGEST, expected_receipt_sha256=RECEIPT_DIGEST, expected_azure_principal_sha256=PRINCIPAL_DIGEST)


def test_cli_failure_is_constant_and_redacts_internal_detail(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(consumer, 'consume_handoff', lambda **_: consumer._fail('secret-token-or-response'))
    args = ['--saved-plan', '/p/plan', '--state-json', '/p/state', '--execution-dir', '/p/execution', '--receipt', '/p/receipt', '--expected-source-commit', SOURCE, '--expected-saved-plan-sha256', PLAN_DIGEST, '--expected-receipt-sha256', RECEIPT_DIGEST, '--expected-azure-principal-sha256', PRINCIPAL_DIGEST]
    assert consumer.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == '' and captured.err == 'recovery_apply_refused\n'
    assert 'secret' not in captured.err
    assert consumer.main(['--unknown', 'secret-input']) == 2
    captured = capsys.readouterr()
    assert captured.out == '' and captured.err == 'recovery_apply_refused\n'
    assert 'secret-input' not in captured.err
    abbreviated = ['--saved-pla', '/p/plan']
    assert consumer.main(abbreviated) == 2
    captured = capsys.readouterr()
    assert captured.out == '' and captured.err == 'recovery_apply_refused\n'

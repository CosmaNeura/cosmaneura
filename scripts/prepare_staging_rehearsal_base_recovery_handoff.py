#!/opt/homebrew/bin/python3 -IS
# ruff: noqa: E701, E702, I001
"""Prepare a read-only, machine-bound staging-rehearsal recovery handoff.

This program can read Azure Resource Manager and the isolated OpenTofu backend.
It has no Azure write method and never applies a plan.  A separate human-only
step must consume the resulting receipt.
"""

# fmt: off
from __future__ import annotations
import sys as _bootstrap_sys
if __name__ == '__main__' and (_bootstrap_sys.flags.isolated != 1 or _bootstrap_sys.flags.no_site != 1 or not _bootstrap_sys.flags.safe_path):
    _bootstrap_sys.stderr.write('recovery_handoff_refused\n')
    raise SystemExit(2)
import argparse
import email.utils
import fcntl
import hashlib
import json
import os
import pwd
import re
import secrets
import ssl
import stat
import sys
import time
import types
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, Protocol
_VALIDATOR_FILE = Path(__file__).resolve().with_name('validate_staging_rehearsal_base_recovery_plan.py')
try:
    recovery = types.ModuleType('_cosmaneura_recovery_validator')
    recovery.__file__ = str(_VALIDATOR_FILE)
    sys.modules[recovery.__name__] = recovery
    exec(compile(_VALIDATOR_FILE.read_bytes(), str(_VALIDATOR_FILE), 'exec', dont_inherit=True), recovery.__dict__)
except Exception:
    print('recovery_handoff_refused', file=sys.stderr)
    raise SystemExit(2)
SUBSCRIPTION = recovery.EXPECTED_SUBSCRIPTION_ID
TENANT = recovery.EXPECTED_TENANT_ID
SUBSCRIPTION_SCOPE = f'/subscriptions/{SUBSCRIPTION}'
CONTROL_GROUP = f'{SUBSCRIPTION_SCOPE}/resourceGroups/cosmaneura-staging-rehearsal-control'
TARGET_GROUP = f'{SUBSCRIPTION_SCOPE}/resourceGroups/cosmaneura-staging-rehearsal'
SOURCE_SERVER = f'{SUBSCRIPTION_SCOPE}/resourceGroups/cosmaneura-staging/providers/Microsoft.DBforPostgreSQL/flexibleServers/cosmaneura-staging-postgres'
ARM_ORIGIN = 'https://management.azure.com'
MAX_ARM_BYTES = 8 * 1024 * 1024
MAX_TOTAL_ARM_BYTES = 256 * 1024 * 1024
MAX_ARM_ITEMS = 4096
MAX_CA_BUNDLE_BYTES = 4 * 1024 * 1024
ARM_RETRY_BACKOFF_SECONDS = (0.05, 0.1)
MAX_ARM_PAGES = 16
MAX_READBACK_SECONDS = 180
MAX_ACTION_READBACK_SECONDS = 120.0
MAX_OBSERVATION_SPAN = timedelta(minutes=2)
MAX_CLOCK_SKEW = timedelta(minutes=5)
RECEIPT_LIFETIME = timedelta(minutes=10)
UUID_RE = re.compile('^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
BASE_TAGS = {'data-classification': 'non-phi-control-metadata', 'environment': 'staging', 'managed-by': 'opentofu', 'purpose': 'staging-restore-rehearsal-control'}
AZURE_CLI_ENTRYPOINTS = (Path('/opt/homebrew/bin/az'), Path('/usr/local/bin/az'), Path('/usr/bin/az'))
PYTHON_ENTRYPOINTS = (Path('/opt/homebrew/bin/python3'), Path('/usr/local/bin/python3'), Path('/usr/bin/python3'))
EXPECTED_RECOVERY_LAUNCHER_SHA256 = '92d1f6a6c8ce3fad565da04be976b3a7fff48f56daa2dbcbbcdfb61a47888f10'
MAX_RECOVERY_LAUNCHER_BYTES = 64 * 1024
REMOTE_STATE_COMPARISON_POLICY = 'canonical-json-check-results-permutation-v1'

class HandoffError(ValueError):
    """A bounded refusal that never includes an Azure response."""

def _fail(code: str) -> NoReturn:
    raise HandoffError(code)

def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()

def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def _lower_id(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith('/'):
        _fail('azure_resource.invalid')
    return value.rstrip('/').lower()

def _id(*parts: str) -> str:
    return '/' + '/'.join((part.strip('/') for part in parts))

def _uuid5(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, value))
ROLE_IDS = {name: _id(SUBSCRIPTION_SCOPE, 'providers/Microsoft.Authorization/roleDefinitions', _uuid5(f'https://github.com/CosmaNeura/cosmaneura/roles/{suffix}')) for name, suffix in {'control_reader': 'staging-restore-control-reader-v1', 'source_reader': 'staging-restore-source-reader-v1', 'target_provisioner': 'staging-restore-target-provisioner-v1', 'target_cleanup': 'staging-restore-target-cleanup-v1', 'marker_reader': 'staging-restore-marker-reader-v1', 'marker_create': 'staging-restore-marker-create-read-v1'}.items()}
IDENTITY_IDS = {'dispatcher_cleanup': _id(CONTROL_GROUP, 'providers/Microsoft.ManagedIdentity/userAssignedIdentities', 'cosmaneura-staging-restore-cleanup'), 'dispatcher_provision': _id(CONTROL_GROUP, 'providers/Microsoft.ManagedIdentity/userAssignedIdentities', 'cosmaneura-staging-restore-provision'), 'job_cleanup': _id(CONTROL_GROUP, 'providers/Microsoft.ManagedIdentity/userAssignedIdentities', 'cosmaneura-staging-restore-cleanup-job'), 'job_provision': _id(CONTROL_GROUP, 'providers/Microsoft.ManagedIdentity/userAssignedIdentities', 'cosmaneura-staging-restore-provision-job')}
STORAGE_ID = _id(CONTROL_GROUP, 'providers/Microsoft.Storage/storageAccounts', 'cosmaneurarehearsalctl')
VNET_ID = _id(CONTROL_GROUP, 'providers/Microsoft.Network/virtualNetworks', 'cosmaneura-staging-rehearsal-control-vnet')
MARKER_SUBNET_ID = _id(VNET_ID, 'subnets', 'marker-private-endpoints')
EXECUTOR_SUBNET_ID = _id(VNET_ID, 'subnets', 'private-executor-reserved')
LATER_SUBNET_ID = _id(VNET_ID, 'subnets', 'private-executor-service-endpoints')
DNS_ID = _id(CONTROL_GROUP, 'providers/Microsoft.Network/privateDnsZones', 'privatelink.blob.core.windows.net')
DNS_LINK_ID = _id(DNS_ID, 'virtualNetworkLinks', 'cosmaneura-staging-rehearsal-marker-link')
POLICY_ID = _id(SUBSCRIPTION_SCOPE, 'providers/Microsoft.Authorization/policyDefinitions', 'deny-staging-restore-firewall-rules')
POLICY_ASSIGNMENT_ID = _id(TARGET_GROUP, 'providers/Microsoft.Authorization/policyAssignments', 'deny-staging-restore-firewall-rules')
FIC_IDS = {kind: _id(IDENTITY_IDS[f'dispatcher_{kind}'], 'federatedIdentityCredentials', f'github-staging-restore-{kind}') for kind in ('cleanup', 'provision')}

def _assignment_id(scope: str, seed: str) -> str:
    return _id(scope, 'providers/Microsoft.Authorization/roleAssignments', _uuid5(seed))
ASSIGNMENT_IDS = {'control_cleanup': _assignment_id(SUBSCRIPTION_SCOPE, f'{SUBSCRIPTION_SCOPE}|cosmaneura-staging-restore-cleanup|control-reader'), 'control_provision': _assignment_id(SUBSCRIPTION_SCOPE, f'{SUBSCRIPTION_SCOPE}|cosmaneura-staging-restore-provision|control-reader'), 'source': _assignment_id(SOURCE_SERVER, f'{SOURCE_SERVER}|provision|source-reader'), 'target_cleanup': _assignment_id(TARGET_GROUP, f'{TARGET_GROUP}|cleanup|target-cleanup'), 'target_provision': _assignment_id(TARGET_GROUP, f'{TARGET_GROUP}|provision|target-provisioner')}
CONTAINER_NAMES = ('restore-cleanups-v3', 'restore-cleanups-v4', 'restore-evidence-v1', 'restore-intents-v3', 'restore-intents-v4', 'restore-submissions-v3', 'restore-submissions-v4')
CONTAINER_IDS = tuple((_id(STORAGE_ID, 'blobServices/default/containers', name) for name in CONTAINER_NAMES))
PRIVATE_ENDPOINT_ID = _id(CONTROL_GROUP, 'providers/Microsoft.Network/privateEndpoints', 'cosmaneura-staging-rehearsal-marker-pe')
MISSING_ASSIGNMENT_IDS = tuple((_assignment_id(scope, seed) for scope, seed in ((_id(STORAGE_ID, 'blobServices/default/containers/restore-cleanups-v4'), 'CosmaNeura/cosmaneura|provision_cleanup|marker-read'), (_id(STORAGE_ID, 'blobServices/default/containers/restore-intents-v4'), 'CosmaNeura/cosmaneura|cleanup_intent|marker-read'), (_id(STORAGE_ID, 'blobServices/default/containers/restore-submissions-v4'), 'CosmaNeura/cosmaneura|cleanup_submission|marker-read'))))
LATER_ASSIGNMENT_IDS = tuple((_assignment_id(_id(STORAGE_ID, 'blobServices/default/containers', container), f'CosmaNeura/cosmaneura|{key}|marker-create-read') for key, container in (('provision_intent', 'restore-intents-v4'), ('provision_submission', 'restore-submissions-v4'), ('cleanup_cleanup', 'restore-cleanups-v4'))))
EXISTING_IDS = {'azurerm_federated_identity_credential.dispatcher["cleanup"]': FIC_IDS['cleanup'], 'azurerm_federated_identity_credential.dispatcher["provision"]': FIC_IDS['provision'], 'azurerm_policy_definition.deny_postgres_firewall_rules[0]': POLICY_ID, 'azurerm_private_dns_zone.marker_blob[0]': DNS_ID, 'azurerm_private_dns_zone_virtual_network_link.marker_blob[0]': DNS_LINK_ID, 'azurerm_resource_group.control[0]': CONTROL_GROUP, 'azurerm_resource_group.target[0]': TARGET_GROUP, 'azurerm_resource_group_policy_assignment.deny_postgres_firewall_rules[0]': POLICY_ASSIGNMENT_ID, 'azurerm_role_assignment.control_reader["cleanup"]': ASSIGNMENT_IDS['control_cleanup'], 'azurerm_role_assignment.control_reader["provision"]': ASSIGNMENT_IDS['control_provision'], 'azurerm_role_assignment.source_reader[0]': ASSIGNMENT_IDS['source'], 'azurerm_role_assignment.target_cleanup[0]': ASSIGNMENT_IDS['target_cleanup'], 'azurerm_role_assignment.target_provisioner[0]': ASSIGNMENT_IDS['target_provision'], **{f'azurerm_role_definition.{name}[0]': role for name, role in ROLE_IDS.items() if name != 'marker_create'}, 'azurerm_storage_account.marker[0]': STORAGE_ID, 'azurerm_subnet.marker_private_endpoint[0]': MARKER_SUBNET_ID, 'azurerm_subnet.private_executor_reserved[0]': EXECUTOR_SUBNET_ID, 'azurerm_user_assigned_identity.dispatcher["cleanup"]': IDENTITY_IDS['dispatcher_cleanup'], 'azurerm_user_assigned_identity.dispatcher["provision"]': IDENTITY_IDS['dispatcher_provision'], 'azurerm_user_assigned_identity.job["cleanup"]': IDENTITY_IDS['job_cleanup'], 'azurerm_user_assigned_identity.job["provision"]': IDENTITY_IDS['job_provision'], 'azurerm_virtual_network.control[0]': VNET_ID}
API = {'resources': '2021-04-01', 'identity': '2023-01-31', 'authorization': '2022-04-01', 'policy': '2023-04-01', 'exemption': '2022-07-01-preview', 'network': '2023-09-01', 'dns': '2020-06-01', 'storage': '2023-05-01', 'locks': '2016-09-01'}
ROLE_CONTRACTS = {
    'control_reader': {'name': 'Cosmaneura staging restore control reader', 'description': 'Read only the subscription metadata required by the staging restore preflight.', 'actions': ('Microsoft.Authorization/policyAssignments/read', 'Microsoft.Authorization/policyDefinitions/read', 'Microsoft.Authorization/policyExemptions/read', 'Microsoft.Authorization/roleAssignments/read', 'Microsoft.Authorization/roleDefinitions/read', 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials/read', 'Microsoft.ManagedIdentity/userAssignedIdentities/read', 'Microsoft.Resources/subscriptions/read', 'Microsoft.Resources/subscriptions/resourceGroups/read', 'Microsoft.Resources/subscriptions/resourcegroups/resources/read'), 'data_actions': ()},
    'source_reader': {'name': 'Cosmaneura staging restore source reader', 'description': 'Read only the exact staging PostgreSQL source and its firewall rules.', 'actions': ('Microsoft.DBforPostgreSQL/flexibleServers/firewallRules/read', 'Microsoft.DBforPostgreSQL/flexibleServers/read'), 'data_actions': ()},
    'target_provisioner': {'name': 'Cosmaneura staging restore target provisioner', 'description': 'Create and isolate one approved PostgreSQL restore inside the dedicated target RG.', 'actions': ('Microsoft.DBforPostgreSQL/flexibleServers/firewallRules/read', 'Microsoft.DBforPostgreSQL/flexibleServers/privateEndpointConnections/read', 'Microsoft.DBforPostgreSQL/flexibleServers/privateEndpointConnectionsApproval/action', 'Microsoft.DBforPostgreSQL/flexibleServers/read', 'Microsoft.DBforPostgreSQL/flexibleServers/write', 'Microsoft.Network/networkInterfaces/read', 'Microsoft.Network/privateDnsZones/read', 'Microsoft.Network/privateDnsZones/virtualNetworkLinks/read', 'Microsoft.Network/privateDnsZones/virtualNetworkLinks/write', 'Microsoft.Network/privateDnsZones/write', 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups/read', 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups/write', 'Microsoft.Network/privateEndpoints/read', 'Microsoft.Network/privateEndpoints/write', 'Microsoft.Network/publicIPAddresses/read', 'Microsoft.Network/virtualNetworks/read', 'Microsoft.Network/virtualNetworks/subnets/join/action', 'Microsoft.Network/virtualNetworks/subnets/read', 'Microsoft.Network/virtualNetworks/subnets/write', 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings/read', 'Microsoft.Network/virtualNetworks/write', 'Microsoft.Resources/tags/write'), 'data_actions': ()},
    'target_cleanup': {'name': 'Cosmaneura staging restore target cleanup', 'description': 'Read and delete only approved restore-envelope resource kinds in the target RG.', 'actions': ('Microsoft.DBforPostgreSQL/flexibleServers/delete', 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules/read', 'Microsoft.DBforPostgreSQL/flexibleServers/privateEndpointConnections/read', 'Microsoft.DBforPostgreSQL/flexibleServers/read', 'Microsoft.Network/networkInterfaces/delete', 'Microsoft.Network/networkInterfaces/read', 'Microsoft.Network/privateDnsZones/delete', 'Microsoft.Network/privateDnsZones/read', 'Microsoft.Network/privateDnsZones/virtualNetworkLinks/delete', 'Microsoft.Network/privateDnsZones/virtualNetworkLinks/read', 'Microsoft.Network/privateEndpoints/delete', 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups/delete', 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups/read', 'Microsoft.Network/privateEndpoints/read', 'Microsoft.Network/publicIPAddresses/read', 'Microsoft.Network/virtualNetworks/delete', 'Microsoft.Network/virtualNetworks/read', 'Microsoft.Network/virtualNetworks/subnets/delete', 'Microsoft.Network/virtualNetworks/subnets/read', 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings/read', 'Microsoft.Resources/tags/read'), 'data_actions': ()},
    'marker_reader': {'name': 'Cosmaneura staging restore marker reader', 'description': 'Read immutable restore markers from an explicitly assigned container only.', 'actions': (), 'data_actions': ('Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read',)},
}
INHERITED_POLICY_ASSIGNMENTS = {
    '/providers/microsoft.management/managementgroups/d51754d0-bba5-4810-820e-afac542844d6/providers/microsoft.authorization/policyassignments/sys.mfa-write': {'scope': '/providers/microsoft.management/managementgroups/d51754d0-bba5-4810-820e-afac542844d6', 'policyDefinitionId': '/providers/microsoft.authorization/policydefinitions/54f51f64-eaa5-44cf-8674-830bcfd14d21', 'enforcementMode': 'Default', 'parameters': {'effect': {'value': 'Audit'}}, 'notScopes': None, 'overrides': None, 'resourceSelectors': None},
    '/providers/microsoft.management/managementgroups/d51754d0-bba5-4810-820e-afac542844d6/providers/microsoft.authorization/policyassignments/sys.mfa-delete': {'scope': '/providers/microsoft.management/managementgroups/d51754d0-bba5-4810-820e-afac542844d6', 'policyDefinitionId': '/providers/microsoft.authorization/policydefinitions/f69cd8b8-9a6e-46b4-be84-244e8b127944', 'enforcementMode': 'Default', 'parameters': {'effect': {'value': 'AuditAction'}}, 'notScopes': None, 'overrides': None, 'resourceSelectors': None},
    '/providers/microsoft.management/managementgroups/d51754d0-bba5-4810-820e-afac542844d6/providers/microsoft.authorization/policyassignments/sys.blockwesteurope': {'scope': '/providers/microsoft.management/managementgroups/d51754d0-bba5-4810-820e-afac542844d6', 'policyDefinitionId': '/providers/microsoft.authorization/policydefinitions/7509877f-d414-4d79-8d1f-d600ea78d087', 'enforcementMode': 'Default', 'parameters': None, 'notScopes': None, 'overrides': [{'kind': 'policyEffect', 'value': 'deny'}], 'resourceSelectors': [{'name': 'SDPRegions', 'selectors': [{'in': ['australiaeast', 'southcentralus2', 'spaincentral2', 'australiacentral2', 'japaneast', 'chilecentral', 'jioindiawest', 'koreasouth', 'austriaeast', 'southeastasia', 'southindia', 'francesouth', 'switzerlandnorth', 'germanynorth', 'belgiumcentral', 'southafricanorth', 'uaenorth', 'polandcentral', 'norwayeast', 'northeurope', 'israelcentral', 'taiwannorthwest', 'ukwest', 'malaysiasouth2', 'qatarcentral', 'mexicocentral', 'italynorth2', 'eastus2', 'centralus', 'canadaeast', 'brazilsouth', 'spaincentral', 'westus3'], 'kind': 'resourceLocation'}]}]},
    f'{SUBSCRIPTION_SCOPE}/providers/microsoft.authorization/policyassignments/securitycenterbuiltin': {'scope': SUBSCRIPTION_SCOPE, 'policyDefinitionId': '/providers/microsoft.authorization/policysetdefinitions/1f3afdf9-d0c9-4c3d-847f-89da613e70a8', 'enforcementMode': 'Default', 'parameters': {}, 'notScopes': None, 'overrides': None, 'resourceSelectors': None},
}
LOCAL_POLICY_ASSIGNMENT = {'scope': TARGET_GROUP, 'policyDefinitionId': POLICY_ID, 'enforcementMode': 'Default', 'parameters': None, 'notScopes': None, 'overrides': None, 'resourceSelectors': None}

class ReadOnlyArm(Protocol):

    @property
    def account(self) -> Mapping[str, Any]:
        ...

    @property
    def azure_cli_sha256(self) -> str:
        ...

    @property
    def azure_cli_version_sha256(self) -> str:
        ...

    @property
    def azure_tls_ca_bundle_sha256(self) -> str:
        ...

    @property
    def azure_config_binding_sha256(self) -> str:
        ...

    @property
    def dates(self) -> list[datetime]:
        ...

    def refresh_account(self) -> None:
        ...

    def begin_action_readback(self, *, deadline: float) -> float:
        ...

    def revalidate_runtime(self) -> None:
        ...

    def get(self, resource_id: str, api_version: str, *, missing: bool=False) -> Mapping[str, Any] | None:
        ...

    def listing(self, resource_id: str, api_version: str, *, query: Mapping[str, str] | None=None) -> Sequence[Mapping[str, Any]]:
        ...

class _NoRedirect(urllib.request.HTTPRedirectHandler):

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None

def _hardened_tls_context() -> tuple[ssl.SSLContext, str]:
    if any(name in os.environ for name in ('SSLKEYLOGFILE', 'SSL_CERT_FILE', 'SSL_CERT_DIR')):
        _fail('tls.environment_invalid')
    configured = ssl.get_default_verify_paths().openssl_cafile
    if not configured:
        _fail('tls.trust_unavailable')
    try:
        path = Path(configured).resolve(strict=True)
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid not in {0, os.geteuid()} or stat.S_IMODE(metadata.st_mode) & 18 or not 0 < metadata.st_size <= MAX_CA_BUNDLE_BYTES:
            _fail('tls.trust_unsafe')
        pem = path.read_bytes()
        if len(pem) != metadata.st_size:
            _fail('tls.trust_unsafe')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.options |= ssl.OP_NO_COMPRESSION
        context.load_verify_locations(cadata=pem.decode('ascii'))
    except (OSError, UnicodeDecodeError, ssl.SSLError):
        _fail('tls.trust_unavailable')
    return (context, _digest(pem))

def _validate_path_ancestry(path: Path, *, leaf_directory: bool=False) -> None:
    if not path.is_absolute() or path != Path(os.path.abspath(path)):
        _fail('local_path.invalid')
    current = Path(path.anchor)
    for index, part in enumerate(path.parts[1:]):
        current /= part
        try:
            metadata = current.lstat()
        except OSError:
            _fail('local_path.unavailable')
        leaf = index == len(path.parts[1:]) - 1
        if metadata.st_uid not in {0, os.geteuid()}:
            _fail('local_path.unsafe')
        if leaf and current.is_symlink():
            continue
        if stat.S_IMODE(metadata.st_mode) & 2 or (stat.S_IMODE(metadata.st_mode) & 16 and metadata.st_uid != os.geteuid()):
            _fail('local_path.unsafe')
        if (leaf and leaf_directory and not stat.S_ISDIR(metadata.st_mode)) or (not leaf and not stat.S_ISDIR(metadata.st_mode)):
            _fail('local_path.invalid')

def _resolve_azure_cli() -> Path:
    for entrypoint in AZURE_CLI_ENTRYPOINTS:
        if not entrypoint.exists():
            continue
        _validate_path_ancestry(entrypoint)
        try:
            executable = entrypoint.resolve(strict=True)
        except OSError:
            _fail('azure_cli.unavailable')
        _validate_path_ancestry(executable)
        metadata = executable.stat()
        if not stat.S_ISREG(metadata.st_mode) or not os.access(executable, os.X_OK) or stat.S_IMODE(metadata.st_mode) & 18:
            _fail('azure_cli.unsafe')
        return executable
    _fail('azure_cli.unavailable')

def _operator_paths() -> tuple[Path, Path, str]:
    try:
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir).resolve(strict=True)
        config, binding = recovery._validated_azure_config(home)
    except (KeyError, OSError, recovery.RecoveryPlanError):
        _fail('azure_cli.environment_invalid')
    _validate_path_ancestry(home, leaf_directory=True)
    _validate_path_ancestry(config, leaf_directory=True)
    return (home, config, binding)

def _python_runtime_binding() -> Mapping[str, str]:
    unsafe = {'LD_PRELOAD', 'LD_LIBRARY_PATH', 'DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH'}
    if any(name.startswith('PYTHON') or name in unsafe for name in os.environ) or sys.flags.isolated != 1 or sys.flags.no_site != 1 or not sys.flags.safe_path or sys.gettrace() is not None or sys.getprofile() is not None or sys.prefix != sys.base_prefix or not (sys.version_info >= (3, 11) and sys.version_info < (4, 0)):
        _fail('python_runtime.unsafe')
    executable = Path(sys.executable).resolve(strict=True)
    allowed = []
    for entrypoint in PYTHON_ENTRYPOINTS:
        if entrypoint.exists():
            _validate_path_ancestry(entrypoint)
            allowed.append(entrypoint.resolve(strict=True))
    if executable not in allowed:
        _fail('python_runtime.untrusted')
    _validate_path_ancestry(executable)
    base = Path(sys.base_prefix).resolve(strict=True)
    paths = []
    for item in sys.path:
        path = Path(item)
        if not item or not path.is_absolute() or 'site-packages' in path.parts or 'dist-packages' in path.parts:
            _fail('python_runtime.path_invalid')
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(base):
            _fail('python_runtime.path_invalid')
        paths.append(str(resolved))
    modules = {'email.utils': email.utils, 'hashlib': hashlib, 'json': json, 'os': os, 'pwd': pwd, 're': re, 'secrets': secrets, 'ssl': ssl, 'stat': stat, 'urllib.error': urllib.error, 'urllib.parse': urllib.parse, 'urllib.request': urllib.request, 'uuid': uuid}
    module_digests = {}
    for name, module in modules.items():
        raw_path = getattr(module, '__file__', None)
        if raw_path is None:
            module_digests[name] = 'built-in'
            continue
        path = Path(raw_path).resolve(strict=True)
        if not path.is_relative_to(base):
            _fail('python_runtime.module_invalid')
        value = path.read_bytes()
        if not value or len(value) > 16 * 1024 * 1024:
            _fail('python_runtime.module_invalid')
        module_digests[name] = _digest(value)
    executable_sha256 = recovery._executable_sha256(executable, label='Python executable')
    runtime_sha256 = _digest(_canonical({'cache_tag': sys.implementation.cache_tag, 'hexversion': sys.hexversion, 'openssl': ssl.OPENSSL_VERSION, 'paths': paths, 'modules': module_digests}))
    return {'python_executable_sha256': executable_sha256, 'python_runtime_sha256': runtime_sha256}

def _reject_ambient_transport_environment() -> None:
    forbidden = {'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'ssl_cert_file', 'ssl_cert_dir', 'curl_ca_bundle', 'requests_ca_bundle'}
    if any(name.lower() in forbidden for name in os.environ):
        _fail('transport_environment.unsafe')

@contextmanager
def _pinned_process_environment(azure_cli: Path, home: Path) -> Any:
    original = dict(os.environ)
    path = ':'.join(dict.fromkeys((str(azure_cli.parent), '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin')))
    os.environ.clear()
    os.environ.update({'HOME': str(home), 'PATH': path, 'LC_ALL': 'C', 'LANG': 'C'})
    try:
        if (Path(path.split(':', 1)[0]) / 'az').resolve(strict=True) != azure_cli:
            _fail('azure_cli.path_mismatch')
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)

class AzureManagementReader:
    """Azure CLI login plus an HTTPS transport that can issue only ARM GETs."""

    def __init__(self, *, azure_cli: Path | None=None, runtime_data_dir: Path | None=None, source_sha256: str | None=None, closure_sha256: str | None=None, runtime_snapshot_sha256: str | None=None) -> None:
        self._action_window_started = False
        self._az = azure_cli or _resolve_azure_cli()
        self._runtime_data_dir, self._closure_sha256, self._runtime_snapshot_sha256 = runtime_data_dir, closure_sha256, runtime_snapshot_sha256
        self._home, self._azure_config, self.azure_config_binding_sha256 = _operator_paths()
        self._az_executable_sha256 = recovery._executable_sha256(self._az, label='Azure CLI')
        self.azure_cli_sha256 = source_sha256 or self._az_executable_sha256
        self.account: Mapping[str, Any] = {}
        self.refresh_account()
        version = self._az_json('version')
        self.azure_cli_version_sha256 = _digest(_canonical(version))
        if _digest(_canonical(self._az_json('cloud'))) != recovery.EXPECTED_AZURE_CLOUD_SHA256: _fail('azure_cloud.invalid')
        token = self._az_json('token')
        access_token = token.get('accessToken')
        if not isinstance(access_token, str) or not 100 < len(access_token) <= 64 * 1024 or token.get('tokenType') != 'Bearer' or (token.get('subscription') != SUBSCRIPTION) or (token.get('tenant') != TENANT):
            _fail('azure_token.invalid')
        self._token = access_token
        self.dates: list[datetime] = []
        self._deadline = time.monotonic() + MAX_READBACK_SECONDS
        self._bytes_read = 0
        context, self.azure_tls_ca_bundle_sha256 = _hardened_tls_context()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(), urllib.request.HTTPSHandler(context=context))

    def _az_json(self, operation: str) -> Mapping[str, Any]:
        commands = {'account': ('account', 'show', '--subscription', SUBSCRIPTION), 'version': ('version',), 'cloud': ('cloud', 'show', '--name', 'AzureCloud'), 'token': ('account', 'get-access-token', '--subscription', SUBSCRIPTION, '--resource', f'{ARM_ORIGIN}/')}
        arguments = commands.get(operation)
        if arguments is None:
            _fail('azure_cli.operation_invalid')
        home, azure_config, binding = _operator_paths()
        if home != self._home or azure_config != self._azure_config or binding != self.azure_config_binding_sha256: _fail('azure_runtime.changed')
        path = ':'.join(dict.fromkeys((str(self._az.parent), '/usr/bin', '/bin')))
        command = ['/usr/bin/env', '-i', f'PATH={path}', 'LC_ALL=C', f'AZURE_CONFIG_DIR={azure_config}', f'{recovery.AZURE_CONFIG_BINDING_ENV}={binding}', *(f'{name}={value}' for name, value in recovery._azure_cli_environment(self._az.parent).items()), str(self._az), *arguments, '--only-show-errors', '--output', 'json']
        timeout = 60.0
        if self._action_window_started:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                _fail('arm_request.timeout')
            timeout = min(timeout, remaining)
        result = recovery._run_process(command, cwd=recovery.REPOSITORY_ROOT, timeout_seconds=timeout, max_output_bytes=256 * 1024)
        if result.returncode != 0:
            _fail('azure_cli.failed')
        value = recovery._load_json_bytes(result.stdout, label='Azure CLI response')
        return value

    def refresh_account(self) -> None:
        self.account = self._az_json('account')

    def begin_action_readback(self, *, deadline: float) -> float:
        now = time.monotonic()
        if self._action_window_started or type(deadline) is not float or not now < deadline <= now + MAX_ACTION_READBACK_SECONDS:
            _fail('arm_request.window_invalid')
        self.dates = []
        self._bytes_read = 0
        self._deadline = deadline
        self._action_window_started = True
        return self._deadline

    def revalidate_runtime(self) -> None:
        runtime_changed = recovery._executable_sha256(self._az, label='Azure CLI') != self._az_executable_sha256
        if self._runtime_data_dir is not None:
            runtime = recovery._snapshot_and_verify_runtime(data_dir=self._runtime_data_dir); runtime_changed = runtime_changed or runtime.azure_cli != self._az or runtime.azure_cli_sha256 != self.azure_cli_sha256 or runtime.azure_cli_closure_sha256 != self._closure_sha256 or runtime.runtime_snapshot_sha256 != self._runtime_snapshot_sha256
        if runtime_changed or _digest(_canonical(self._az_json('version'))) != self.azure_cli_version_sha256 or _digest(_canonical(self._az_json('cloud'))) != recovery.EXPECTED_AZURE_CLOUD_SHA256 or _hardened_tls_context()[1] != self.azure_tls_ca_bundle_sha256 or _operator_paths()[2] != self.azure_config_binding_sha256:
            _fail('azure_runtime.changed')

    def _url(self, resource_id: str, api_version: str, query: Mapping[str, str] | None=None) -> str:
        role_collection = _id(SUBSCRIPTION_SCOPE, 'providers/Microsoft.Authorization/roleAssignments')
        container_collection = _id(STORAGE_ID, 'blobServices/default/containers')
        filter_value = (query or {}).get('$filter', '')
        filter_match = re.fullmatch("principalId eq '([0-9a-f-]{36})'", filter_value) if isinstance(filter_value, str) else None
        valid_filter = query is not None and set(query) == {'$filter'} and resource_id == role_collection and api_version == API['authorization'] and filter_match is not None and UUID_RE.fullmatch(filter_match.group(1)) is not None
        valid_deleted = query == {'$include': 'deleted'} and resource_id == container_collection and api_version == API['storage']
        if not resource_id.startswith(f'{SUBSCRIPTION_SCOPE}/') or '?' in resource_id or api_version not in API.values() or (query is not None and not valid_filter and not valid_deleted):
            _fail('arm_request.invalid')
        parameters = {'api-version': api_version, **(query or {})}
        return f"{ARM_ORIGIN}{urllib.parse.quote(resource_id, safe='/')}?{urllib.parse.urlencode(parameters)}"

    def _request(self, url: str, *, missing: bool) -> Mapping[str, Any] | None:
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            _fail('arm_request.invalid')
        if parsed.scheme != 'https' or parsed.netloc != 'management.azure.com' or (not parsed.path.startswith(f'{SUBSCRIPTION_SCOPE}/')):
            _fail('arm_request.invalid')
        request = urllib.request.Request(url, headers={'Authorization': f'Bearer {self._token}', 'Accept': 'application/json', 'Accept-Encoding': 'identity'}, method='GET')
        raw = None
        lengths: list[str] = []
        for attempt in range(3):
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                _fail('arm_request.timeout')
            response = None
            try:
                response = self._opener.open(request, timeout=min(20, remaining))
                if response.status != 200 or response.geturl() != url:
                    _fail('arm_response.invalid')
                self._record_date(response.headers)
                content_types = response.headers.get_all('Content-Type') or []
                encodings = response.headers.get_all('Content-Encoding') or []
                lengths = response.headers.get_all('Content-Length') or []
                if len(content_types) != 1 or content_types[0].split(';', 1)[0].strip().lower() != 'application/json' or len(encodings) > 1 or (encodings and encodings[0].lower() != 'identity') or (len(lengths) > 1) or (lengths and (not lengths[0].isdigit() or int(lengths[0]) > MAX_ARM_BYTES)):
                    _fail('arm_response.invalid')
                raw = response.read(MAX_ARM_BYTES + 1)
            except urllib.error.HTTPError as exc:
                self._record_date(exc.headers)
                if missing and exc.code == 404:
                    exc.close()
                    return None
                exc.close()
                _fail('arm_request.failed')
            except (OSError, urllib.error.URLError, TimeoutError):
                if response is not None:
                    response.close()
                if attempt == 2:
                    _fail('arm_request.failed')
                delay = min(ARM_RETRY_BACKOFF_SECONDS[attempt], max(0.0, self._deadline - time.monotonic()))
                if delay <= 0:
                    _fail('arm_request.timeout')
                time.sleep(delay)
                continue
            except ValueError:
                if response is not None:
                    response.close()
                _fail('arm_response.invalid')
            response.close()
            break
        if raw is None:
            _fail('arm_request.failed')
        if not raw or len(raw) > MAX_ARM_BYTES or (lengths and int(lengths[0]) != len(raw)):
            _fail('arm_response.invalid')
        self._bytes_read += len(raw)
        if self._bytes_read > MAX_TOTAL_ARM_BYTES:
            _fail('arm_response.total_too_large')
        try:

            def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                value: dict[str, Any] = {}
                for key, item in pairs:
                    if key in value:
                        _fail('arm_response.invalid')
                    value[key] = item
                return value
            value = json.loads(raw, object_pairs_hook=reject_duplicates, parse_constant=lambda _: _fail('arm_response.invalid'))
        except (UnicodeDecodeError, ValueError, RecursionError):
            _fail('arm_response.invalid')
        if not isinstance(value, Mapping):
            _fail('arm_response.invalid')
        return value

    def _record_date(self, headers: Any) -> None:
        try:
            values = headers.get_all('Date')
            parsed = email.utils.parsedate_to_datetime(values[0])
        except (AttributeError, IndexError, TypeError, ValueError):
            _fail('arm_response.date_invalid')
        if len(values) != 1 or parsed.utcoffset() != timedelta(0):
            _fail('arm_response.date_invalid')
        self.dates.append(parsed.astimezone(UTC))

    def get(self, resource_id: str, api_version: str, *, missing: bool=False) -> Mapping[str, Any] | None:
        value = self._request(self._url(resource_id, api_version), missing=missing)
        if missing != (value is None):
            _fail('azure_presence.mismatch')
        return value

    def listing(self, resource_id: str, api_version: str, *, query: Mapping[str, str] | None=None) -> list[Mapping[str, Any]]:
        url = self._url(resource_id, api_version, query)
        initial = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query, strict_parsing=True, max_num_fields=16)
        values: list[Mapping[str, Any]] = []
        seen_urls: set[str] = set()
        seen_ids: set[str] = set()
        for _ in range(MAX_ARM_PAGES):
            if url in seen_urls:
                _fail('arm_listing.repeated_page')
            seen_urls.add(url)
            page = self._request(url, missing=False)
            if not isinstance(page, Mapping) or not isinstance(page.get('value'), list) or (not all((isinstance(item, Mapping) for item in page['value']))):
                _fail('arm_listing.invalid')
            page_ids = [_lower_id(item.get('id')) for item in page['value']]
            if len(page_ids) != len(set(page_ids)) or seen_ids.intersection(page_ids) or len(values) + len(page_ids) > MAX_ARM_ITEMS:
                _fail('arm_listing.invalid')
            seen_ids.update(page_ids)
            values.extend(page['value'])
            next_link = page.get('nextLink')
            if next_link is None:
                return values
            if not isinstance(next_link, str):
                _fail('arm_listing.invalid')
            try:
                parsed = urllib.parse.urlsplit(next_link)
                current = urllib.parse.urlsplit(url)
                parameters = urllib.parse.parse_qs(parsed.query, strict_parsing=True, max_num_fields=16)
                tokens = [name for name in ('$skipToken', '$skiptoken') if name in parameters]
                invalid_authority = parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)
            except ValueError:
                _fail('arm_listing.invalid')
            if len(next_link) > 16384 or parsed.scheme != 'https' or parsed.netloc != 'management.azure.com' or parsed.fragment or invalid_authority or parsed.path != current.path or len(tokens) != 1 or set(parameters) - (set(initial) | set(tokens)) or any((parameters.get(name) != value for name, value in initial.items())) or len(parameters[tokens[0]]) != 1 or not parameters[tokens[0]][0] or len(parameters[tokens[0]][0]) > 8192:
                _fail('arm_listing.invalid')
            url = next_link
        _fail('arm_listing.too_many_pages')

def _state_attributes(state_bytes: bytes) -> dict[str, Mapping[str, Any]]:
    state = recovery._load_json_bytes(state_bytes, label='state snapshot')
    try:
        recovery._validate_state(state, label='state snapshot')
    except recovery.RecoveryPlanError as exc:
        if 'resource identity does not match the exact contract' in str(exc):
            _fail('state_resource_id.mismatch')
        _fail('state_snapshot.invalid')
    output: dict[str, Mapping[str, Any]] = {}
    for resource in state['resources']:
        for instance in resource['instances']:
            output[recovery._state_address(resource, instance)] = instance['attributes']
    for address, expected in EXISTING_IDS.items():
        attributes = output[address]
        observed = attributes.get('id')
        role_definition = address.startswith('azurerm_role_definition.')
        expected_state_id = f'{expected}|{SUBSCRIPTION_SCOPE}' if role_definition else expected
        if _lower_id(observed) != _lower_id(expected_state_id):
            _fail('state_resource_id.mismatch')
        if role_definition:
            key = address.removeprefix('azurerm_role_definition.').removesuffix('[0]')
            contract = ROLE_CONTRACTS[key]
            if _lower_id(attributes.get('role_definition_resource_id')) != _lower_id(expected) or attributes.get('name') != contract['name'] or attributes.get('description') != contract['description'] or _permission_digest(attributes.get('permissions')) != _permission_digest([{'actions': contract['actions'], 'notActions': [], 'dataActions': contract['data_actions'], 'notDataActions': []}]):
                _fail('state_role_definition.mismatch')
    return output

def _remote_state_digest(state: Mapping[str, Any]) -> str:
    # OpenTofu emits these top-level records in map-iteration order. Preserve
    # each complete record and every other list; normalize only this ordering.
    normalized = dict(state)
    checks = state.get('check_results')
    if checks is not None:
        if not isinstance(checks, list):
            _fail('remote_state.check_results_invalid')
        seen: set[tuple[str, str]] = set()
        for check in checks:
            if not isinstance(check, Mapping):
                _fail('remote_state.check_results_invalid')
            kind, address = check.get('object_kind'), check.get('config_addr')
            if not isinstance(kind, str) or not kind or not isinstance(address, str) or not address or (kind, address) in seen:
                _fail('remote_state.check_results_invalid')
            seen.add((kind, address))
        normalized['check_results'] = sorted(checks, key=_canonical)
    return _digest(_canonical(normalized))

def _pull_remote_state(*, tofu: Path, data_dir: Path, expected_state: Mapping[str, Any], expected_backend_sha256: str, expected_tofu_sha256: str) -> str:
    if not tofu.is_absolute() or tofu != Path(os.path.abspath(tofu)) or recovery._executable_sha256(tofu, label='OpenTofu executable') != expected_tofu_sha256:
        _fail('runtime.changed')
    if recovery._backend_snapshot(tofu=tofu, data_dir=data_dir) != expected_backend_sha256:
        _fail('backend.changed')
    result = recovery._run_process([str(tofu), f'-chdir={recovery.REHEARSAL_ROOT}', 'state', 'pull'], cwd=recovery.REPOSITORY_ROOT, timeout_seconds=60, max_output_bytes=recovery.MAXIMUM_ARTIFACT_BYTES, data_dir=data_dir)
    if result.returncode != 0:
        _fail('remote_state.unavailable')
    remote = recovery._load_json_bytes(result.stdout, label='remote state')
    recovery._validate_state(remote, label='remote state')
    remote_digest = _remote_state_digest(remote)
    if remote_digest != _remote_state_digest(expected_state):
        _fail('remote_state.changed')
    return remote_digest

def _properties(value: Mapping[str, Any], expected_id: str) -> Mapping[str, Any]:
    if _lower_id(value.get('id')) != _lower_id(expected_id) or not isinstance(value.get('properties', {}), Mapping):
        _fail('azure_resource.mismatch')
    return value.get('properties', {})

def _tags(value: Mapping[str, Any], expected: Mapping[str, str]) -> None:
    if value.get('tags') != expected:
        _fail('azure_tags.mismatch')

def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value

def _permission_digest(value: Any) -> str:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 1:
        _fail('role_definition.mismatch')
    normalized = []
    aliases = {'actions': 'actions', 'notActions': 'not_actions', 'dataActions': 'data_actions', 'notDataActions': 'not_data_actions'}
    for permission in value:
        if not isinstance(permission, Mapping) or set(permission) - (set(aliases) | set(aliases.values())):
            _fail('role_definition.mismatch')
        normalized.append({live: sorted(permission.get(live, permission.get(state, [])) or []) for live, state in aliases.items()})
    return _digest(_canonical(sorted(normalized, key=_canonical)))

def _capture_snapshot(reader: ReadOnlyArm, attributes: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
    account = reader.account
    if account.get('id') != SUBSCRIPTION or account.get('tenantId') != TENANT or account.get('environmentName') != 'AzureCloud' or (account.get('state') != 'Enabled') or (not isinstance(account.get('user'), Mapping)) or (account['user'].get('type') != 'user') or (not isinstance(account['user'].get('name'), str)) or (not account['user']['name']):
        _fail('azure_account.mismatch')
    projection: dict[str, Any] = {'account': {'subscription': SUBSCRIPTION, 'tenant': TENANT, 'principal_sha256': _digest(str(account['user'].get('name', '')).encode())}}
    for address, resource_id, location, tags in (('azurerm_resource_group.control[0]', CONTROL_GROUP, 'eastus', BASE_TAGS), ('azurerm_resource_group.target[0]', TARGET_GROUP, 'northcentralus', {**BASE_TAGS, 'purpose': 'staging-restore-rehearsal-ephemeral-target'})):
        raw = reader.get(resource_id, API['resources'])
        assert raw is not None
        _properties(raw, resource_id)
        if str(raw.get('location', '')).replace(' ', '').lower() != location:
            _fail('resource_group.mismatch')
        _tags(raw, tags)
        projection[address] = _lower_id(resource_id)
    identity_by_address = {'azurerm_user_assigned_identity.dispatcher["cleanup"]': 'dispatcher_cleanup', 'azurerm_user_assigned_identity.dispatcher["provision"]': 'dispatcher_provision', 'azurerm_user_assigned_identity.job["cleanup"]': 'job_cleanup', 'azurerm_user_assigned_identity.job["provision"]': 'job_provision'}
    principals: dict[str, str] = {}
    for address, key in identity_by_address.items():
        raw = reader.get(IDENTITY_IDS[key], API['identity'])
        assert raw is not None
        props = _properties(raw, IDENTITY_IDS[key])
        state = attributes[address]
        if raw.get('location') != 'eastus' or props.get('tenantId') != TENANT or props.get('principalId') != state.get('principal_id') or (props.get('clientId') != state.get('client_id')) or (not UUID_RE.fullmatch(str(props.get('principalId', '')))) or (not UUID_RE.fullmatch(str(props.get('clientId', '')))):
            _fail('managed_identity.mismatch')
        _tags(raw, BASE_TAGS)
        principals[key] = str(props['principalId'])
        projection[address] = _digest(_canonical({'id': _lower_id(raw['id']), 'principal': props['principalId'], 'client': props['clientId']}))
    if len(set(principals.values())) != 4 or len({attributes[address]['client_id'] for address in identity_by_address}) != 4:
        _fail('managed_identity.mismatch')
    for kind in ('cleanup', 'provision'):
        address = f'azurerm_federated_identity_credential.dispatcher["{kind}"]'
        raw = reader.get(FIC_IDS[kind], API['identity'])
        assert raw is not None
        props = _properties(raw, FIC_IDS[kind])
        expected = {'issuer': 'https://token.actions.githubusercontent.com', 'subject': f'repo:CosmaNeura/cosmaneura:environment:staging-restore-{kind}', 'audiences': ['api://AzureADTokenExchange']}
        if {name: props.get(name) for name in expected} != expected:
            _fail('federated_credential.mismatch')
        projection[address] = _digest(_canonical(expected))
    for key, identity_id in IDENTITY_IDS.items():
        expected_ids = {FIC_IDS[key.removeprefix('dispatcher_')].lower()} if key.startswith('dispatcher_') else set()
        observed = {_lower_id(item.get('id')) for item in reader.listing(_id(identity_id, 'federatedIdentityCredentials'), API['identity'])}
        if observed != expected_ids:
            _fail('federated_credential.inventory_mismatch')
    role_addresses = {address: role for address, role in EXISTING_IDS.items() if address.startswith('azurerm_role_definition.')}
    for address, role_id in role_addresses.items():
        raw = reader.get(role_id, API['authorization'])
        assert raw is not None
        props = _properties(raw, role_id)
        key = address.removeprefix('azurerm_role_definition.').removesuffix('[0]')
        contract = ROLE_CONTRACTS[key]
        expected_permissions = [{'actions': contract['actions'], 'notActions': [], 'dataActions': contract['data_actions'], 'notDataActions': []}]
        if props.get('type') != 'CustomRole' or props.get('roleName') != contract['name'] or props.get('description') != contract['description'] or (props.get('assignableScopes') != [SUBSCRIPTION_SCOPE]) or (_permission_digest(props.get('permissions')) != _permission_digest(expected_permissions)):
            _fail('role_definition.mismatch')
        projection[address] = _digest(_canonical({'id': _lower_id(role_id), 'permissions': _permission_digest(props['permissions'])}))
    assignment_specs = {'azurerm_role_assignment.control_reader["cleanup"]': ('dispatcher_cleanup', ROLE_IDS['control_reader'], SUBSCRIPTION_SCOPE), 'azurerm_role_assignment.control_reader["provision"]': ('dispatcher_provision', ROLE_IDS['control_reader'], SUBSCRIPTION_SCOPE), 'azurerm_role_assignment.source_reader[0]': ('job_provision', ROLE_IDS['source_reader'], SOURCE_SERVER), 'azurerm_role_assignment.target_cleanup[0]': ('job_cleanup', ROLE_IDS['target_cleanup'], TARGET_GROUP), 'azurerm_role_assignment.target_provisioner[0]': ('job_provision', ROLE_IDS['target_provisioner'], TARGET_GROUP)}
    assignments_by_principal = {principal: set() for principal in principals.values()}
    for address, (identity, role_id, scope) in assignment_specs.items():
        resource_id = EXISTING_IDS[address]
        raw = reader.get(resource_id, API['authorization'])
        assert raw is not None
        props = _properties(raw, resource_id)
        expected = {'principalId': principals[identity], 'principalType': 'ServicePrincipal', 'roleDefinitionId': role_id, 'scope': scope, 'description': '', 'condition': None, 'conditionVersion': None, 'delegatedManagedIdentityResourceId': None}
        if {name: props.get(name) for name in expected} != expected:
            _fail('role_assignment.mismatch')
        assignments_by_principal[principals[identity]].add(_lower_id(resource_id))
        projection[address] = _digest(_canonical(expected))
    role_collection = _id(SUBSCRIPTION_SCOPE, 'providers/Microsoft.Authorization/roleAssignments')
    for principal, expected in assignments_by_principal.items():
        observed_items = reader.listing(role_collection, API['authorization'], query={'$filter': f"principalId eq '{principal}'"})
        if any((_properties(item, str(item.get('id'))).get('principalId') != principal for item in observed_items)) or {_lower_id(item.get('id')) for item in observed_items} != expected:
            _fail('role_assignment.inventory_mismatch')
    expected_assignments = {EXISTING_IDS[address].lower(): {'principalId': principals[identity], 'principalType': 'ServicePrincipal', 'roleDefinitionId': role_id.lower(), 'scope': scope.lower(), 'description': '', 'condition': None, 'conditionVersion': None, 'delegatedManagedIdentityResourceId': None} for address, (identity, role_id, scope) in assignment_specs.items()}
    observed_assignments: dict[str, Mapping[str, Any]] = {}
    custom_roles = {value.lower() for value in ROLE_IDS.values()}
    for item in reader.listing(role_collection, API['authorization']):
        props = _properties(item, str(item.get('id')))
        role_id = props.get('roleDefinitionId')
        if isinstance(role_id, str) and role_id.lower() in custom_roles:
            item_id = _lower_id(item.get('id'))
            principal, scope = props.get('principalId'), props.get('scope')
            if not isinstance(principal, str) or not isinstance(scope, str):
                _fail('role_assignment.inventory_mismatch')
            observed_assignments[item_id] = {'principalId': principal, 'principalType': props.get('principalType'), 'roleDefinitionId': role_id.lower(), 'scope': scope.lower(), 'description': props.get('description'), 'condition': props.get('condition'), 'conditionVersion': props.get('conditionVersion'), 'delegatedManagedIdentityResourceId': props.get('delegatedManagedIdentityResourceId')}
    if observed_assignments != expected_assignments:
        _fail('role_assignment.inventory_mismatch')
    policy = reader.get(POLICY_ID, API['policy'])
    assert policy is not None
    policy_props = _properties(policy, POLICY_ID)
    rule = {'if': {'field': 'type', 'equals': 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules'}, 'then': {'effect': 'deny'}}
    metadata = policy_props.get('metadata')
    if policy_props.get('policyType') != 'Custom' or policy_props.get('mode') != 'All' or policy_props.get('displayName') != 'Deny PostgreSQL firewall rules in the staging restore target' or (policy_props.get('description') != 'Fail closed if any restore path attempts to create a public PostgreSQL firewall rule.') or (not isinstance(metadata, Mapping)) or ({name: metadata.get(name) for name in ('category', 'version')} != {'category': 'Cosmaneura staging restore', 'version': '1.0.0'}) or (policy_props.get('policyRule') != rule):
        _fail('deny_policy.mismatch')
    assignment = reader.get(POLICY_ASSIGNMENT_ID, API['policy'])
    assert assignment is not None
    assignment_props = _properties(assignment, POLICY_ASSIGNMENT_ID)
    policy_definition_id = assignment_props.get('policyDefinitionId')
    if not isinstance(policy_definition_id, str) or policy_definition_id.lower() != POLICY_ID.lower() or assignment_props.get('description') != 'Deny every PostgreSQL Flexible Server firewall-rule resource in the ephemeral restore target RG.' or assignment_props.get('enforcementMode', 'Default') != 'Default' or any((assignment_props.get(name) not in (None, [], {}) for name in ('notScopes', 'parameters', 'overrides', 'resourceSelectors'))):
        _fail('deny_policy_assignment.mismatch')
    expected_policies = {POLICY_ASSIGNMENT_ID.lower(): LOCAL_POLICY_ASSIGNMENT, **INHERITED_POLICY_ASSIGNMENTS}
    expected_policies = {item_id: {**contract, 'scope': str(contract['scope']).lower(), 'policyDefinitionId': str(contract['policyDefinitionId']).lower()} for item_id, contract in expected_policies.items()}
    effective_policies: dict[str, Mapping[str, Any]] = {}
    for item in reader.listing(_id(TARGET_GROUP, 'providers/Microsoft.Authorization/policyAssignments'), API['policy']):
        item_id = _lower_id(item.get('id'))
        props = _properties(item, str(item.get('id')))
        scope, definition = props.get('scope'), props.get('policyDefinitionId')
        if not isinstance(scope, str) or not isinstance(definition, str) or not definition.startswith('/'):
            _fail('target_policy.inventory_mismatch')
        effective_policies[item_id] = {'scope': scope.lower(), 'policyDefinitionId': definition.lower(), 'enforcementMode': props.get('enforcementMode', 'Default'), 'parameters': props.get('parameters'), 'notScopes': props.get('notScopes'), 'overrides': props.get('overrides'), 'resourceSelectors': props.get('resourceSelectors')}
    if effective_policies != expected_policies or reader.listing(_id(TARGET_GROUP, 'providers/Microsoft.Authorization/denyAssignments'), API['authorization']):
        _fail('target_authorization.inventory_mismatch')
    projection['deny_policy'] = _digest(_canonical({'rule': rule, 'assignments': effective_policies}))
    storage = reader.get(STORAGE_ID, API['storage'])
    assert storage is not None
    storage_props = _properties(storage, STORAGE_ID)
    expected_storage = {'allowBlobPublicAccess': False, 'allowCrossTenantReplication': False, 'allowSharedKeyAccess': False, 'defaultToOAuthAuthentication': True, 'supportsHttpsTrafficOnly': True, 'isHnsEnabled': False, 'isLocalUserEnabled': False, 'isNfsV3Enabled': False, 'isSftpEnabled': False, 'minimumTlsVersion': 'TLS1_2', 'publicNetworkAccess': 'Disabled', 'provisioningState': 'Succeeded'}
    if storage.get('location') != 'eastus' or storage.get('kind') != 'StorageV2' or _mapping(storage.get('sku'), 'storage_account.mismatch').get('name') != 'Standard_ZRS' or any((storage_props.get(name) != value for name, value in expected_storage.items())):
        _fail('storage_account.mismatch')
    encryption = _mapping(storage_props.get('encryption'), 'storage_account.mismatch')
    network = _mapping(storage_props.get('networkAcls'), 'storage_account.mismatch')
    if encryption.get('requireInfrastructureEncryption') is not True or network.get('defaultAction') != 'Deny' or network.get('bypass') != 'None' or storage_props.get('privateEndpointConnections') != [] or any((network.get(name) not in (None, []) for name in ('ipRules', 'virtualNetworkRules', 'resourceAccessRules'))):
        _fail('storage_account.mismatch')
    _tags(storage, BASE_TAGS)
    blob = reader.get(_id(STORAGE_ID, 'blobServices/default'), API['storage'])
    assert blob is not None
    blob_props = _properties(blob, _id(STORAGE_ID, 'blobServices/default'))
    change_feed = blob_props.get('changeFeed')
    last_access = blob_props.get('lastAccessTimeTrackingPolicy')
    if (change_feed is not None and _mapping(change_feed, 'blob_service.mismatch').get('enabled') is not False) or (last_access is not None and _mapping(last_access, 'blob_service.mismatch').get('enable') is not False) or blob_props.get('isVersioningEnabled') not in (None, False) or _mapping(blob_props.get('cors'), 'blob_service.mismatch').get('corsRules') != [] or _mapping(blob_props.get('deleteRetentionPolicy'), 'blob_service.mismatch') != {'enabled': False, 'allowPermanentDelete': False}:
        _fail('blob_service.mismatch')
    projection['storage'] = _digest(_canonical({**expected_storage, 'encryption': True, 'network': ['Deny', 'None']}))
    vnet = reader.get(VNET_ID, API['network'])
    assert vnet is not None
    vnet_props = _properties(vnet, VNET_ID)
    if vnet.get('location') != 'eastus' or _mapping(vnet_props.get('addressSpace'), 'control_network.mismatch').get('addressPrefixes') != ['10.76.0.0/24'] or _mapping(vnet_props.get('dhcpOptions'), 'control_network.mismatch').get('dnsServers', []) != [] or (vnet_props.get('enableDdosProtection', False) is not False) or (vnet_props.get('ddosProtectionPlan') not in (None, {})):
        _fail('control_network.mismatch')
    _tags(vnet, BASE_TAGS)
    subnet_contracts = {MARKER_SUBNET_ID: ('10.76.0.0/27', 'Disabled', 'Disabled'), EXECUTOR_SUBNET_ID: ('10.76.0.32/27', 'Enabled', 'Enabled')}
    for resource_id, (prefix, endpoint_policy, service_policy) in subnet_contracts.items():
        subnet = reader.get(resource_id, API['network'])
        assert subnet is not None
        props = _properties(subnet, resource_id)
        if props.get('addressPrefix') != prefix or props.get('defaultOutboundAccess') is not False or props.get('privateEndpointNetworkPolicies') != endpoint_policy or (props.get('privateLinkServiceNetworkPolicies') != service_policy) or (props.get('delegations', []) != []) or (props.get('serviceEndpoints', []) != []) or any((props.get(name) not in (None, {}, []) for name in ('networkSecurityGroup', 'routeTable', 'natGateway', 'ipConfigurations', 'privateEndpoints', 'applicationGatewayIPConfigurations', 'serviceEndpointPolicies'))):
            _fail('control_subnet.mismatch')
    if {_lower_id(item.get('id')) for item in reader.listing(_id(VNET_ID, 'subnets'), API['network'])} != {MARKER_SUBNET_ID.lower(), EXECUTOR_SUBNET_ID.lower()}:
        _fail('control_subnet.inventory_mismatch')
    if reader.listing(_id(VNET_ID, 'virtualNetworkPeerings'), API['network']):
        _fail('control_network.peering_present')
    projection['network'] = _digest(_canonical(subnet_contracts))
    zone = reader.get(DNS_ID, API['dns'])
    link = reader.get(DNS_LINK_ID, API['dns'])
    assert zone is not None and link is not None
    _properties(zone, DNS_ID)
    link_props = _properties(link, DNS_LINK_ID)
    linked_vnet = _mapping(link_props.get('virtualNetwork'), 'private_dns.mismatch').get('id')
    if zone.get('location') != 'global' or link.get('location') != 'global' or not isinstance(linked_vnet, str) or linked_vnet.lower() != VNET_ID.lower() or (link_props.get('registrationEnabled') is not False) or (link_props.get('provisioningState') != 'Succeeded'):
        _fail('private_dns.mismatch')
    _tags(zone, BASE_TAGS)
    _tags(link, BASE_TAGS)
    if {_lower_id(item.get('id')) for item in reader.listing(_id(DNS_ID, 'virtualNetworkLinks'), API['dns'])} != {DNS_LINK_ID.lower()}:
        _fail('private_dns.inventory_mismatch')
    if {_lower_id(item.get('id')) for item in reader.listing(_id(DNS_ID, 'ALL'), API['dns'])} != {_id(DNS_ID, 'SOA', '@').lower()}:
        _fail('private_dns.records_mismatch')
    projection['dns'] = _digest(_canonical({'zone': _lower_id(DNS_ID), 'link': _lower_id(DNS_LINK_ID)}))
    control_inventory = reader.listing(_id(CONTROL_GROUP, 'resources'), API['resources'])
    expected_control = {IDENTITY_IDS[name].lower() for name in IDENTITY_IDS} | {DNS_ID.lower(), DNS_LINK_ID.lower(), VNET_ID.lower(), STORAGE_ID.lower()}
    if {_lower_id(item.get('id')) for item in control_inventory} != expected_control:
        _fail('control_inventory.mismatch')
    if reader.listing(_id(TARGET_GROUP, 'resources'), API['resources']):
        _fail('target_inventory.not_empty')
    if reader.listing(_id(TARGET_GROUP, 'providers/Microsoft.Authorization/policyExemptions'), API['exemption']):
        _fail('policy_exemption.present')
    if reader.listing(_id(STORAGE_ID, 'blobServices/default/containers'), API['storage'], query={'$include': 'deleted'}):
        _fail('container_inventory.not_empty')
    if reader.listing(_id(CONTROL_GROUP, 'providers/Microsoft.Network/privateEndpoints'), API['network']):
        _fail('private_endpoint.present')
    subscription_locks = reader.listing(_id(SUBSCRIPTION_SCOPE, 'providers/Microsoft.Authorization/locks'), API['locks'])
    marker = '/providers/microsoft.authorization/locks/'
    if any(_lower_id(item.get('id')).partition(marker)[1] != marker or _lower_id(item.get('id')).partition(marker)[0] == SUBSCRIPTION_SCOPE.lower() for item in subscription_locks) or any(reader.listing(_id(scope, 'providers/Microsoft.Authorization/locks'), API['locks']) for scope in (CONTROL_GROUP, TARGET_GROUP, STORAGE_ID)):
        _fail('management_lock.present')
    for resource_id, api in (*((value, API['storage']) for value in CONTAINER_IDS), (PRIVATE_ENDPOINT_ID, API['network']), *((value, API['authorization']) for value in MISSING_ASSIGNMENT_IDS), (LATER_SUBNET_ID, API['network']), (ROLE_IDS['marker_create'], API['authorization']), *((value, API['authorization']) for value in LATER_ASSIGNMENT_IDS)):
        reader.get(resource_id, api, missing=True)
    projection['absence'] = _digest(_canonical({'planned': [*CONTAINER_IDS, PRIVATE_ENDPOINT_ID, *MISSING_ASSIGNMENT_IDS], 'later': [LATER_SUBNET_ID, ROLE_IDS['marker_create'], *LATER_ASSIGNMENT_IDS]}))
    return projection

def _boot_session() -> bytes:
    if sys.platform.startswith('linux'):
        value = Path('/proc/sys/kernel/random/boot_id').read_bytes().strip()
    elif sys.platform == 'darwin':
        result = recovery._run_process(['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid'], cwd=recovery.REPOSITORY_ROOT, timeout_seconds=10, max_output_bytes=256)
        if result.returncode != 0:
            _fail('machine_binding.unavailable')
        value = result.stdout.strip()
    else:
        _fail('machine_binding.unavailable')
    if not value or len(value) > 256:
        _fail('machine_binding.unavailable')
    return value

def _source_binding(expected_commit: str, expected_tree_sha256: str) -> str:
    snapshot = recovery._source_snapshot(expected_commit)
    if snapshot.tree_sha256 != expected_tree_sha256:
        _fail('source.changed')
    path = Path(__file__).resolve()
    relative = path.relative_to(recovery.REPOSITORY_ROOT)
    source = path.read_bytes()
    if recovery._run_git(['status', '--porcelain=v1', '--untracked-files=all', '--', str(relative)]) or recovery._run_git(['show', f'{expected_commit}:{relative}']) != source:
        _fail('handoff_source.changed')
    return _digest(source)

def _open_verified_recovery_launcher() -> int:
    flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    source: int | None = None
    snapshot_reader: int | None = None
    snapshot_writer: int | None = None
    descriptor: int | None = None
    try:
        source = os.open(recovery.LAUNCHER_PATH, flags)
        before = os.fstat(source)
        linked = os.stat(recovery.LAUNCHER_PATH, follow_symlinks=False)
        def identity(value: os.stat_result) -> tuple[int, ...]: return value.st_dev, value.st_ino, value.st_uid, value.st_nlink, stat.S_IMODE(value.st_mode), value.st_size, value.st_mtime_ns, value.st_ctime_ns
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o755 or not 0 < before.st_size <= MAX_RECOVERY_LAUNCHER_BYTES or identity(before) != identity(linked):
            _fail('plan_launcher.invalid')
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(source, min(64 * 1024, remaining))
            if not chunk:
                _fail('plan_launcher.invalid')
            chunks.append(chunk)
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(source, 1):
            _fail('plan_launcher.invalid')
        after = os.fstat(source)
        linked_after = os.stat(recovery.LAUNCHER_PATH, follow_symlinks=False)
        if identity(before) != identity(after) or identity(after) != identity(linked_after) or digest.hexdigest() != EXPECTED_RECOVERY_LAUNCHER_SHA256:
            _fail('plan_launcher.invalid')
        snapshot = b''.join(chunks)
        if len(snapshot) != before.st_size or hashlib.sha256(snapshot).hexdigest() != EXPECTED_RECOVERY_LAUNCHER_SHA256:
            _fail('plan_launcher.invalid')
        snapshot_reader, snapshot_writer = os.pipe()
        writer_flags = fcntl.fcntl(snapshot_writer, fcntl.F_GETFL)
        fcntl.fcntl(snapshot_writer, fcntl.F_SETFL, writer_flags | os.O_NONBLOCK)
        offset = 0
        while offset < len(snapshot):
            written = os.write(snapshot_writer, snapshot[offset:])
            if written <= 0:
                _fail('plan_launcher.invalid')
            offset += written
        os.close(snapshot_writer)
        snapshot_writer = None
        descriptor = fcntl.fcntl(snapshot_reader, getattr(fcntl, 'F_DUPFD_CLOEXEC', fcntl.F_DUPFD), 10)
        os.close(snapshot_reader)
        snapshot_reader = None
        anonymous = os.fstat(descriptor)
        if not stat.S_ISFIFO(anonymous.st_mode) or anonymous.st_uid != os.geteuid() or (fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE) != os.O_RDONLY:
            _fail('plan_launcher.invalid')
        result = descriptor
        descriptor = None
        return result
    except (OSError, HandoffError):
        _fail('plan_launcher.invalid')
    finally:
        for open_descriptor in (source, snapshot_reader, snapshot_writer, descriptor):
            if open_descriptor is not None:
                os.close(open_descriptor)

def _revalidate_plan_via_launcher(*, saved_plan: Path, state_json: Path, execution_dir: Path, expected_source_commit: str, expected_saved_plan_sha256: str, existing_execution_dir: bool=False) -> Mapping[str, Any]:
    descriptor = _open_verified_recovery_launcher()
    try:
        result = recovery._run_process(
            [
                '/bin/sh', f'/dev/fd/{descriptor}',
                '--authenticated-launcher-sha256', EXPECTED_RECOVERY_LAUNCHER_SHA256,
                '--mode', 'verify-existing' if existing_execution_dir else 'verify',
                '--saved-plan', str(saved_plan), '--state-json', str(state_json),
                '--execution-dir', str(execution_dir),
                '--expected-source-commit', expected_source_commit,
                '--expected-saved-plan-sha256', expected_saved_plan_sha256,
            ],
            cwd=recovery.REPOSITORY_ROOT,
            timeout_seconds=900,
            pass_fds=(descriptor,),
            max_output_bytes=64 * 1024,
        )
    finally:
        os.close(descriptor)
    if result.returncode != 0 or result.stderr or not result.stdout:
        _fail('plan_validation.refused')
    try:
        receipt = recovery._load_json_bytes(result.stdout, label='base validation receipt')
    except recovery.RecoveryPlanError:
        _fail('plan_validation.invalid')
    if _canonical(receipt) + b'\n' != result.stdout:
        _fail('plan_validation.invalid')
    if receipt.get('launcher_execution_authenticated') is not False:
        _fail('plan_validation.invalid')
    authenticated = dict(receipt)
    authenticated.update(
        python_launcher_source_path=recovery.LAUNCHER_REPOSITORY_PATH,
        python_launcher_sha256=EXPECTED_RECOVERY_LAUNCHER_SHA256,
        launcher_execution_authenticated=True,
    )
    return authenticated

def _trusted_reader(reader_factory: Callable[[], ReadOnlyArm], *, data_dir: Path, base: Mapping[str, Any]) -> ReadOnlyArm:
    if reader_factory is not AzureManagementReader: return reader_factory()
    runtime = recovery._snapshot_and_verify_runtime(data_dir=data_dir)
    expected = {'open_tofu_sha256': runtime.tofu_sha256, 'azure_cli_sha256': runtime.azure_cli_sha256, 'azure_cli_version_sha256': runtime.azure_cli_version_sha256, 'azure_cli_closure_sha256': runtime.azure_cli_closure_sha256, 'runtime_snapshot_sha256': runtime.runtime_snapshot_sha256}
    if any(base.get(name) != value for name, value in expected.items()) or base.get('azure_cli_source_closure_sha256') != recovery.EXPECTED_AZURE_CLI_CLOSURE_SHA256: _fail('plan_runtime.changed')
    return AzureManagementReader(azure_cli=runtime.azure_cli, runtime_data_dir=data_dir, source_sha256=runtime.azure_cli_sha256, closure_sha256=runtime.azure_cli_closure_sha256, runtime_snapshot_sha256=runtime.runtime_snapshot_sha256)

def prepare_handoff(*, saved_plan: Path, state_json: Path, execution_dir: Path, receipt_path: Path, expected_source_commit: str, expected_saved_plan_sha256: str, reader_factory: Callable[[], ReadOnlyArm]=AzureManagementReader, clock: Callable[[], datetime]=lambda: datetime.now(UTC), nonce_source: Callable[[int], bytes]=secrets.token_bytes, boot_session: Callable[[], bytes]=_boot_session, source_binding: Callable[[str, str], str]=_source_binding) -> Mapping[str, Any]:
    _reject_ambient_transport_environment()
    python_binding = _python_runtime_binding()
    azure_cli = _resolve_azure_cli()
    home, _, _ = _operator_paths()
    with _pinned_process_environment(azure_cli, home):
        return _prepare_handoff(saved_plan=saved_plan, state_json=state_json, execution_dir=execution_dir, receipt_path=receipt_path, expected_source_commit=expected_source_commit, expected_saved_plan_sha256=expected_saved_plan_sha256, reader_factory=reader_factory, clock=clock, nonce_source=nonce_source, boot_session=boot_session, source_binding=source_binding, python_binding=python_binding)

def _prepare_handoff(*, saved_plan: Path, state_json: Path, execution_dir: Path, receipt_path: Path, expected_source_commit: str, expected_saved_plan_sha256: str, reader_factory: Callable[[], ReadOnlyArm], clock: Callable[[], datetime], nonce_source: Callable[[int], bytes], boot_session: Callable[[], bytes], source_binding: Callable[[str, str], str], python_binding: Mapping[str, Any]) -> Mapping[str, Any]:
    if receipt_path.parent != saved_plan.parent or receipt_path.exists():
        _fail('receipt_path.invalid')
    if expected_saved_plan_sha256 in recovery.REJECTED_SAVED_PLAN_SHA256S:
        _fail('saved_plan.rejected')
    base = dict(_revalidate_plan_via_launcher(saved_plan=saved_plan, state_json=state_json, execution_dir=execution_dir, expected_source_commit=expected_source_commit, expected_saved_plan_sha256=expected_saved_plan_sha256))
    recovery_python_binding = {
        'python_executable_sha256': recovery.EXPECTED_PYTHON_SHA256,
        'python_framework_sha256': recovery.EXPECTED_PYTHON_FRAMEWORK_SHA256,
        'python_runtime_manifest_sha256': recovery.EXPECTED_PYTHON_RUNTIME_MANIFEST_SHA256,
        'python_runtime_snapshot_policy': recovery.PYTHON_RUNTIME_SNAPSHOT_POLICY,
        'python_runtime_snapshotted': True,
        'python_tree_acl_entry_count': recovery.EXPECTED_PYTHON_TREE_ACL_ENTRY_COUNT,
        'python_tree_acl_sha256': recovery.EXPECTED_PYTHON_TREE_ACL_SHA256,
        'python_tree_content_sha256': recovery.EXPECTED_PYTHON_TREE_CONTENT_SHA256,
        'python_tree_metadata_sha256': recovery.EXPECTED_PYTHON_TREE_METADATA_SHA256,
        'python_version_sha256': recovery.EXPECTED_PYTHON_VERSION_SHA256,
        'python_runtime_sha256': recovery.EXPECTED_PYTHON_RUNTIME_SHA256,
    }
    if base.get('schema_version') != 3 or base.get('policy_id') != 'cosmaneura-staging-rehearsal-base-recovery-plan-v3' or any(base.get(name) != value for name, value in recovery_python_binding.items()) or base.get('python_executable_source_path') != str(recovery.EXPECTED_PYTHON_PATH) or base.get('python_framework_source_path') != str(recovery.EXPECTED_PYTHON_FRAMEWORK_PATH) or base.get('python_runtime_manifest_source_path') != recovery.PYTHON_RUNTIME_MANIFEST_REPOSITORY_PATH or base.get('python_launcher_source_path') != recovery.LAUNCHER_REPOSITORY_PATH or base.get('python_launcher_sha256') != EXPECTED_RECOVERY_LAUNCHER_SHA256 or base.get('launcher_execution_authenticated') is not True or base.get('live_readback_bound') is not False or base.get('apply_authorized') is not False or base.get('state_serial') != recovery.EXPECTED_RECOVERY_SERIAL or (base.get('existing_resource_count') != 28) or (base.get('create_count') != 11) or (base.get('pass') is not True):
        _fail('plan_receipt.invalid')
    state_bytes = recovery._read_protected_file(state_json, label='state snapshot', required_mode=384)
    if _digest(state_bytes) != base.get('state_json_sha256'):
        _fail('state_snapshot.changed')
    attributes = _state_attributes(state_bytes)
    expected_state = recovery._load_json_bytes(state_bytes, label='state snapshot')
    tofu = Path(str(base.get('open_tofu_path', '')))
    remote_state_sha256 = _pull_remote_state(tofu=tofu, data_dir=execution_dir, expected_state=expected_state, expected_backend_sha256=str(base['backend_metadata_sha256']), expected_tofu_sha256=str(base['open_tofu_sha256']))
    handoff_source_sha256 = source_binding(expected_source_commit, str(base['source_tree_sha256']))
    nonce = nonce_source(32)
    if type(nonce) is not bytes or len(nonce) != 32:
        _fail('machine_binding.invalid')
    nonce_path = execution_dir / '.base-recovery-handoff-nonce'
    recovery._write_protected_file(nonce_path, nonce, label='handoff nonce')
    if recovery._read_protected_file(nonce_path, label='handoff nonce', required_mode=256) != nonce:
        _fail('machine_binding.invalid')
    directory = os.stat(execution_dir, follow_symlinks=False)
    if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid() or stat.S_IMODE(directory.st_mode) != 448:
        _fail('execution_directory.changed')
    reader = _trusted_reader(reader_factory, data_dir=execution_dir, base=base)
    if base.get('azure_cli_sha256') != reader.azure_cli_sha256 or base.get('azure_cli_version_sha256') != reader.azure_cli_version_sha256: _fail('plan_runtime.changed')
    first = _capture_snapshot(reader, attributes)
    reader.refresh_account()
    second = _capture_snapshot(reader, attributes)
    if _canonical(first) != _canonical(second):
        _fail('live_snapshot.changed')
    reader.revalidate_runtime()
    if _pull_remote_state(tofu=tofu, data_dir=execution_dir, expected_state=expected_state, expected_backend_sha256=str(base['backend_metadata_sha256']), expected_tofu_sha256=str(base['open_tofu_sha256'])) != remote_state_sha256 or recovery._provider_tree_sha256(execution_dir) != base['execution_provider_tree_sha256']:
        _fail('runtime.changed')
    directory_after = os.stat(execution_dir, follow_symlinks=False)
    if (directory_after.st_dev, directory_after.st_ino, directory_after.st_uid, stat.S_IMODE(directory_after.st_mode)) != (directory.st_dev, directory.st_ino, directory.st_uid, stat.S_IMODE(directory.st_mode)) or recovery._read_protected_file(nonce_path, label='handoff nonce', required_mode=256) != nonce or recovery._read_protected_file(state_json, label='state snapshot', required_mode=384) != state_bytes or _digest(recovery._read_protected_file(saved_plan, label='saved plan', required_mode=256)) != base['saved_plan_sha256']:
        _fail('machine_binding.changed')
    now = clock().astimezone(UTC)
    if not reader.dates:
        _fail('live_snapshot.time_missing')
    oldest, newest = (min(reader.dates), max(reader.dates))
    if newest - oldest > MAX_OBSERVATION_SPAN or newest > now + MAX_CLOCK_SKEW or now - newest > MAX_CLOCK_SKEW:
        _fail('live_snapshot.stale')
    if source_binding(expected_source_commit, str(base['source_tree_sha256'])) != handoff_source_sha256:
        _fail('source.changed')
    if _canonical(_python_runtime_binding()) != _canonical(python_binding):
        _fail('python_runtime.changed')
    machine = _digest(_canonical({'boot': _digest(boot_session()), 'euid': os.geteuid(), 'execution_device': directory.st_dev, 'execution_inode': directory.st_ino, 'nonce_sha256': _digest(nonce)}))
    receipt = {'schema_version': 1, 'policy_id': 'cosmaneura-staging-rehearsal-base-recovery-handoff-v1', 'source_commit': base['source_commit'], 'source_tree_sha256': base['source_tree_sha256'], 'handoff_source_sha256': handoff_source_sha256, 'provider_lock_sha256': base['provider_lock_sha256'], 'state_lineage': base['state_lineage'], 'state_serial': base['state_serial'], 'state_json_sha256': base['state_json_sha256'], 'state_projection_sha256': base['state_projection_sha256'], 'remote_state_sha256': remote_state_sha256, 'saved_plan_sha256': base['saved_plan_sha256'], 'derived_plan_json_sha256': base['derived_plan_json_sha256'], 'backend_metadata_sha256': base['backend_metadata_sha256'], 'open_tofu_sha256': base['open_tofu_sha256'], 'execution_provider_tree_sha256': base['execution_provider_tree_sha256'], **python_binding, 'azure_cli_sha256': reader.azure_cli_sha256, 'azure_cli_version_sha256': reader.azure_cli_version_sha256, 'azure_cli_source_closure_sha256': base['azure_cli_source_closure_sha256'], 'azure_cli_closure_sha256': base['azure_cli_closure_sha256'], 'runtime_snapshot_sha256': base['runtime_snapshot_sha256'], 'azure_tls_ca_bundle_sha256': reader.azure_tls_ca_bundle_sha256, 'azure_config_binding_sha256': reader.azure_config_binding_sha256, 'base_validation_receipt_sha256': _digest(_canonical(base)), 'machine_binding_sha256': machine, 'nonce_sha256': _digest(nonce), 'live_snapshot_sha256': _digest(_canonical(first)), 'observed_at': newest.isoformat().replace('+00:00', 'Z'), 'expires_at': (newest + RECEIPT_LIFETIME).isoformat().replace('+00:00', 'Z'), 'existing_azure_resource_count': 26, 'planned_absent_resource_count': 11, 'target_resource_count': 0, 'live_readback_bound': True, 'apply_authorized': False, 'pass': True}
    receipt['remote_state_comparison_policy'] = REMOTE_STATE_COMPARISON_POLICY
    payload = _canonical(receipt) + b'\n'
    recovery._write_protected_file(receipt_path, payload, label='handoff receipt')
    if recovery._read_protected_file(receipt_path, label='handoff receipt', required_mode=256) != payload:
        _fail('receipt_write.failed')
    return receipt

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--saved-plan', required=True, type=Path)
    parser.add_argument('--state-json', required=True, type=Path)
    parser.add_argument('--execution-dir', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    parser.add_argument('--expected-source-commit', required=True)
    parser.add_argument('--expected-saved-plan-sha256', required=True)
    return parser

def main(argv: Sequence[str] | None=None) -> int:
    args = _parser().parse_args(argv)
    try:
        receipt = prepare_handoff(saved_plan=args.saved_plan, state_json=args.state_json, execution_dir=args.execution_dir, receipt_path=args.receipt, expected_source_commit=args.expected_source_commit, expected_saved_plan_sha256=args.expected_saved_plan_sha256)
    except Exception:
        print('recovery_handoff_refused', file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(',', ':')))
    return 0
if __name__ == '__main__':
    raise SystemExit(main())

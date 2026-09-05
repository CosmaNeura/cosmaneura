#!/usr/bin/env python3
# ruff: noqa: I001
# fmt: off
from __future__ import annotations
import hashlib
import inspect
import json
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.error
import urllib.request
from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from typing import Any, Mapping
import pytest
from scripts import prepare_staging_rehearsal_base_recovery_handoff as handoff
NOW = datetime(2026, 9, 5, 4, 0, tzinfo=UTC)
PRINCIPALS = {key: f'00000000-0000-0000-0000-{index:012d}' for index, key in enumerate(handoff.IDENTITY_IDS, 1)}
CLIENTS = {key: f'10000000-0000-0000-0000-{index:012d}' for index, key in enumerate(handoff.IDENTITY_IDS, 1)}

def _resource(resource_id: str, properties: Mapping[str, Any] | None=None, **outer: Any) -> dict[str, Any]:
    return {'id': resource_id, 'properties': dict(properties or {}), **outer}

def _state_resource(address: str, attributes: Mapping[str, Any]) -> dict[str, Any]:
    base, separator, raw_key = address.partition('[')
    resource_type, name = base.split('.', 1)
    instance: dict[str, Any] = {'schema_version': 0, 'attributes': dict(attributes)}
    if separator:
        instance['index_key'] = json.loads(raw_key[:-1])
    provider = 'provider["terraform.io/builtin/terraform"]' if resource_type == 'terraform_data' else 'provider["registry.opentofu.org/hashicorp/azurerm"]'
    return {'mode': 'managed', 'type': resource_type, 'name': name, 'provider': provider, 'instances': [instance]}

def _valid_state() -> dict[str, Any]:
    resources = []
    identity_addresses = {'azurerm_user_assigned_identity.dispatcher["cleanup"]': 'dispatcher_cleanup', 'azurerm_user_assigned_identity.dispatcher["provision"]': 'dispatcher_provision', 'azurerm_user_assigned_identity.job["cleanup"]': 'job_cleanup', 'azurerm_user_assigned_identity.job["provision"]': 'job_provision'}
    for address in sorted(handoff.recovery.EXPECTED_EXISTING_ADDRESSES):
        attributes: dict[str, Any] = {'id': handoff.EXISTING_IDS.get(address, address)}
        if address in identity_addresses:
            key = identity_addresses[address]
            attributes.update(principal_id=PRINCIPALS[key], client_id=CLIENTS[key])
        if address.startswith('azurerm_role_definition.'):
            key = address.removeprefix('azurerm_role_definition.').removesuffix('[0]')
            contract = handoff.ROLE_CONTRACTS[key]
            attributes.update(role_definition_resource_id=handoff.EXISTING_IDS[address], name=contract['name'], description=contract['description'], permissions=[{'actions': list(contract['actions']), 'not_actions': [], 'data_actions': list(contract['data_actions']), 'not_data_actions': []}])
        resources.append(_state_resource(address, attributes))
    return {'version': 4, 'terraform_version': handoff.recovery.EXPECTED_TOFU_VERSION, 'serial': 8, 'lineage': handoff.recovery.EXPECTED_LINEAGE, 'outputs': {}, 'resources': resources}

class FakeReader:
    azure_cli_sha256 = 'a' * 64
    azure_cli_version_sha256 = 'b' * 64
    azure_tls_ca_bundle_sha256 = 'c' * 64
    azure_config_binding_sha256 = 'd' * 64

    def __init__(self, state: dict[str, Any] | None=None, *, date: datetime=NOW) -> None:
        self.account: dict[str, Any] = {'id': handoff.SUBSCRIPTION, 'tenantId': handoff.TENANT, 'environmentName': 'AzureCloud', 'state': 'Enabled', 'user': {'type': 'user', 'name': 'operator@example.invalid'}}
        self.dates: list[datetime] = []
        self.date = date
        self.gets: dict[str, dict[str, Any]] = {}
        self.lists: dict[tuple[str, str, tuple[tuple[str, str], ...]], list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, str, bool]] = []
        self._install(state or _valid_state())

    @staticmethod
    def _key(resource_id: str, api: str, query: Mapping[str, str] | None=None) -> tuple[str, str, tuple[tuple[str, str], ...]]:
        return (resource_id.lower(), api, tuple(sorted((query or {}).items())))

    def _install(self, state: dict[str, Any]) -> None:
        attributes = handoff._state_attributes(json.dumps(state).encode())
        for resource_id, location, tags in ((handoff.CONTROL_GROUP, 'eastus', handoff.BASE_TAGS), (handoff.TARGET_GROUP, 'northcentralus', {**handoff.BASE_TAGS, 'purpose': 'staging-restore-rehearsal-ephemeral-target'})):
            self.gets[resource_id.lower()] = _resource(resource_id, {}, location=location, tags=tags)
        identity_addresses = {'dispatcher_cleanup': 'azurerm_user_assigned_identity.dispatcher["cleanup"]', 'dispatcher_provision': 'azurerm_user_assigned_identity.dispatcher["provision"]', 'job_cleanup': 'azurerm_user_assigned_identity.job["cleanup"]', 'job_provision': 'azurerm_user_assigned_identity.job["provision"]'}
        for key, resource_id in handoff.IDENTITY_IDS.items():
            state_values = attributes[identity_addresses[key]]
            self.gets[resource_id.lower()] = _resource(resource_id, {'tenantId': handoff.TENANT, 'principalId': state_values['principal_id'], 'clientId': state_values['client_id']}, location='eastus', tags=handoff.BASE_TAGS)
        for kind, resource_id in handoff.FIC_IDS.items():
            self.gets[resource_id.lower()] = _resource(resource_id, {'issuer': 'https://token.actions.githubusercontent.com', 'subject': f'repo:CosmaNeura/cosmaneura:environment:staging-restore-{kind}', 'audiences': ['api://AzureADTokenExchange']})
        for key, identity_id in handoff.IDENTITY_IDS.items():
            items = [self.gets[handoff.FIC_IDS[key.removeprefix('dispatcher_')].lower()]] if key.startswith('dispatcher_') else []
            self.lists[self._key(handoff._id(identity_id, 'federatedIdentityCredentials'), handoff.API['identity'])] = items
        for address, resource_id in handoff.EXISTING_IDS.items():
            if not address.startswith('azurerm_role_definition.'):
                continue
            permissions = attributes[address]['permissions']
            live = [{'actions': item['actions'], 'notActions': item['not_actions'], 'dataActions': item['data_actions'], 'notDataActions': item['not_data_actions']} for item in permissions]
            self.gets[resource_id.lower()] = _resource(resource_id, {'type': 'CustomRole', 'roleName': attributes[address]['name'], 'description': attributes[address]['description'], 'assignableScopes': [handoff.SUBSCRIPTION_SCOPE], 'permissions': live})
        assignment_specs = {'control_cleanup': ('dispatcher_cleanup', 'control_reader', handoff.SUBSCRIPTION_SCOPE), 'control_provision': ('dispatcher_provision', 'control_reader', handoff.SUBSCRIPTION_SCOPE), 'source': ('job_provision', 'source_reader', handoff.SOURCE_SERVER), 'target_cleanup': ('job_cleanup', 'target_cleanup', handoff.TARGET_GROUP), 'target_provision': ('job_provision', 'target_provisioner', handoff.TARGET_GROUP)}
        by_principal = {value: [] for value in PRINCIPALS.values()}
        all_role_assignments = []
        for key, (identity, role, scope) in assignment_specs.items():
            raw = _resource(handoff.ASSIGNMENT_IDS[key], {'principalId': PRINCIPALS[identity], 'principalType': 'ServicePrincipal', 'roleDefinitionId': handoff.ROLE_IDS[role], 'scope': scope, 'description': '', 'condition': None, 'conditionVersion': None, 'delegatedManagedIdentityResourceId': None})
            self.gets[handoff.ASSIGNMENT_IDS[key].lower()] = raw
            by_principal[PRINCIPALS[identity]].append(raw)
            all_role_assignments.append(raw)
        collection = handoff._id(handoff.SUBSCRIPTION_SCOPE, 'providers/Microsoft.Authorization/roleAssignments')
        for principal, values in by_principal.items():
            query = {'$filter': f"principalId eq '{principal}'"}
            self.lists[self._key(collection, handoff.API['authorization'], query)] = values
        rule = {'if': {'field': 'type', 'equals': 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules'}, 'then': {'effect': 'deny'}}
        self.gets[handoff.POLICY_ID.lower()] = _resource(handoff.POLICY_ID, {'policyType': 'Custom', 'mode': 'All', 'displayName': 'Deny PostgreSQL firewall rules in the staging restore target', 'description': 'Fail closed if any restore path attempts to create a public PostgreSQL firewall rule.', 'metadata': {'category': 'Cosmaneura staging restore', 'version': '1.0.0'}, 'policyRule': rule})
        self.gets[handoff.POLICY_ASSIGNMENT_ID.lower()] = _resource(handoff.POLICY_ASSIGNMENT_ID, {**handoff.LOCAL_POLICY_ASSIGNMENT, 'description': 'Deny every PostgreSQL Flexible Server firewall-rule resource in the ephemeral restore target RG.'})
        storage = {'allowBlobPublicAccess': False, 'allowCrossTenantReplication': False, 'allowSharedKeyAccess': False, 'defaultToOAuthAuthentication': True, 'supportsHttpsTrafficOnly': True, 'isHnsEnabled': False, 'isLocalUserEnabled': False, 'isNfsV3Enabled': False, 'isSftpEnabled': False, 'minimumTlsVersion': 'TLS1_2', 'publicNetworkAccess': 'Disabled', 'provisioningState': 'Succeeded', 'privateEndpointConnections': [], 'encryption': {'requireInfrastructureEncryption': True}, 'networkAcls': {'defaultAction': 'Deny', 'bypass': 'None', 'ipRules': [], 'virtualNetworkRules': [], 'resourceAccessRules': []}}
        self.gets[handoff.STORAGE_ID.lower()] = _resource(handoff.STORAGE_ID, storage, location='eastus', kind='StorageV2', sku={'name': 'Standard_ZRS'}, tags=handoff.BASE_TAGS)
        blob_id = handoff._id(handoff.STORAGE_ID, 'blobServices/default')
        self.gets[blob_id.lower()] = _resource(blob_id, {'cors': {'corsRules': []}, 'deleteRetentionPolicy': {'enabled': False, 'allowPermanentDelete': False}})
        self.gets[handoff.VNET_ID.lower()] = _resource(handoff.VNET_ID, {'addressSpace': {'addressPrefixes': ['10.76.0.0/24']}, 'dhcpOptions': {'dnsServers': []}, 'enableDdosProtection': False}, location='eastus', tags=handoff.BASE_TAGS)
        for resource_id, prefix, endpoint, service in ((handoff.MARKER_SUBNET_ID, '10.76.0.0/27', 'Disabled', 'Disabled'), (handoff.EXECUTOR_SUBNET_ID, '10.76.0.32/27', 'Enabled', 'Enabled')):
            self.gets[resource_id.lower()] = _resource(resource_id, {'addressPrefix': prefix, 'defaultOutboundAccess': False, 'privateEndpointNetworkPolicies': endpoint, 'privateLinkServiceNetworkPolicies': service, 'delegations': [], 'serviceEndpoints': []})
        self.gets[handoff.DNS_ID.lower()] = _resource(handoff.DNS_ID, {}, location='global', tags=handoff.BASE_TAGS)
        self.gets[handoff.DNS_LINK_ID.lower()] = _resource(handoff.DNS_LINK_ID, {'virtualNetwork': {'id': handoff.VNET_ID}, 'registrationEnabled': False, 'provisioningState': 'Succeeded'}, location='global', tags=handoff.BASE_TAGS)
        simple_lists = {(handoff._id(handoff.VNET_ID, 'subnets'), handoff.API['network']): [self.gets[handoff.MARKER_SUBNET_ID.lower()], self.gets[handoff.EXECUTOR_SUBNET_ID.lower()]], (handoff._id(handoff.VNET_ID, 'virtualNetworkPeerings'), handoff.API['network']): [], (handoff._id(handoff.DNS_ID, 'virtualNetworkLinks'), handoff.API['dns']): [self.gets[handoff.DNS_LINK_ID.lower()]], (handoff._id(handoff.CONTROL_GROUP, 'resources'), handoff.API['resources']): [_resource(value) for value in (*handoff.IDENTITY_IDS.values(), handoff.DNS_ID, handoff.DNS_LINK_ID, handoff.VNET_ID, handoff.STORAGE_ID)], (handoff._id(handoff.TARGET_GROUP, 'resources'), handoff.API['resources']): [], (handoff._id(handoff.TARGET_GROUP, 'providers/Microsoft.Authorization/policyExemptions'), handoff.API['exemption']): [], (handoff._id(handoff.CONTROL_GROUP, 'providers/Microsoft.Network/privateEndpoints'), handoff.API['network']): [], (handoff._id(handoff.STORAGE_ID, 'providers/Microsoft.Authorization/locks'), handoff.API['locks']): []}
        for (resource_id, api), value in simple_lists.items():
            self.lists[self._key(resource_id, api)] = value
        self.lists[self._key(handoff._id(handoff.STORAGE_ID, 'blobServices/default/containers'), handoff.API['storage'], {'$include': 'deleted'})] = []
        self.lists[self._key(collection, handoff.API['authorization'])] = all_role_assignments
        inherited = [_resource(item_id, deepcopy(contract)) for item_id, contract in handoff.INHERITED_POLICY_ASSIGNMENTS.items()]
        self.lists[self._key(handoff._id(handoff.TARGET_GROUP, 'providers/Microsoft.Authorization/policyAssignments'), handoff.API['policy'])] = [self.gets[handoff.POLICY_ASSIGNMENT_ID.lower()], *inherited]
        self.lists[self._key(handoff._id(handoff.TARGET_GROUP, 'providers/Microsoft.Authorization/denyAssignments'), handoff.API['authorization'])] = []
        self.lists[self._key(handoff._id(handoff.DNS_ID, 'ALL'), handoff.API['dns'])] = [_resource(handoff._id(handoff.DNS_ID, 'SOA', '@'))]
        for scope in (handoff.SUBSCRIPTION_SCOPE, handoff.CONTROL_GROUP, handoff.TARGET_GROUP):
            self.lists[self._key(handoff._id(scope, 'providers/Microsoft.Authorization/locks'), handoff.API['locks'])] = []

    def get(self, resource_id: str, api_version: str, *, missing: bool=False) -> Mapping[str, Any] | None:
        self.dates.append(self.date)
        self.calls.append((resource_id, api_version, missing))
        value = self.gets.get(resource_id.lower())
        if missing:
            if value is not None:
                handoff._fail('azure_presence.mismatch')
            return None
        if value is None:
            handoff._fail('azure_presence.mismatch')
        return deepcopy(value)

    def refresh_account(self) -> None:
        pass

    def revalidate_runtime(self) -> None:
        pass

    def listing(self, resource_id: str, api_version: str, *, query: Mapping[str, str] | None=None) -> list[dict[str, Any]]:
        self.dates.append(self.date)
        key = self._key(resource_id, api_version, query)
        if key not in self.lists:
            handoff._fail('unexpected_listing')
        return deepcopy(self.lists[key])

def _attributes(state: dict[str, Any] | None=None) -> dict[str, Mapping[str, Any]]:
    return handoff._state_attributes(json.dumps(state or _valid_state()).encode())

def _base_receipt(state_bytes: bytes) -> dict[str, Any]:
    return {'source_commit': 'a' * 40, 'source_tree_sha256': '1' * 64, 'provider_lock_sha256': '2' * 64, 'state_lineage': handoff.recovery.EXPECTED_LINEAGE, 'state_serial': 8, 'state_json_sha256': hashlib.sha256(state_bytes).hexdigest(), 'saved_plan_sha256': hashlib.sha256(b'plan').hexdigest(), 'derived_plan_json_sha256': '4' * 64, 'backend_metadata_sha256': '5' * 64, 'open_tofu_path': '/trusted/tofu', 'open_tofu_sha256': '6' * 64, 'execution_provider_tree_sha256': '7' * 64, 'live_readback_bound': False, 'apply_authorized': False, 'existing_resource_count': 28, 'create_count': 11, 'pass': True}

def _mock_runtime(monkeypatch: pytest.MonkeyPatch, *states: bytes) -> list[list[str]]:
    queue = list(states)
    calls: list[list[str]] = []
    monkeypatch.setattr(handoff.recovery, '_executable_sha256', lambda *_, **__: '6' * 64)
    monkeypatch.setattr(handoff.recovery, '_backend_snapshot', lambda **_: '5' * 64)
    monkeypatch.setattr(handoff.recovery, '_provider_tree_sha256', lambda _: '7' * 64)
    monkeypatch.setattr(handoff, '_python_runtime_binding', lambda: {'python_executable_sha256': '8' * 64, 'python_runtime_sha256': '9' * 64})
    monkeypatch.setattr(handoff, '_resolve_azure_cli', lambda: Path('/trusted/az'))
    monkeypatch.setattr(handoff, '_operator_paths', lambda: (Path('/trusted/home'), Path('/trusted/home/.azure'), 'd' * 64))
    monkeypatch.setattr(handoff, '_pinned_process_environment', lambda *_: nullcontext())
    def run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        assert arguments == ['/trusted/tofu', f'-chdir={handoff.recovery.REHEARSAL_ROOT}', 'state', 'pull'] and kwargs['data_dir'].name == 'execution'
        return subprocess.CompletedProcess(arguments, 0, queue.pop(0), b'')
    monkeypatch.setattr(handoff.recovery, '_run_process', run)
    return calls

def test_exact_contract_has_26_existing_and_11_planned_absent() -> None:
    assert len(handoff.EXISTING_IDS) == 26
    assert len(handoff.CONTAINER_IDS) + 1 + len(handoff.MISSING_ASSIGNMENT_IDS) == 11
    assert set(handoff.EXISTING_IDS) == handoff.recovery.EXPECTED_EXISTING_ADDRESSES - {'terraform_data.activation_guard[0]', 'terraform_data.private_executor_topology_blocker[0]'}

def test_valid_snapshot_is_deterministic_and_checks_every_absence() -> None:
    reader = FakeReader()
    first = handoff._capture_snapshot(reader, _attributes())
    second = handoff._capture_snapshot(reader, _attributes())
    assert handoff._canonical(first) == handoff._canonical(second)
    missing = {resource_id.lower() for resource_id, _, expected_missing in reader.calls if expected_missing}
    assert missing == {value.lower() for value in (*handoff.CONTAINER_IDS, handoff.PRIVATE_ENDPOINT_ID, *handoff.MISSING_ASSIGNMENT_IDS, handoff.LATER_SUBNET_ID, handoff.ROLE_IDS['marker_create'], *handoff.LATER_ASSIGNMENT_IDS)}
    assert not any((value in json.dumps(first) for value in PRINCIPALS.values()))

@pytest.mark.parametrize('case', ['account', 'identity', 'fic', 'role', 'role_assignment', 'role_condition', 'unexpected_grant', 'policy', 'policy_semantics', 'extra_policy', 'storage', 'storage_pe', 'vnet', 'subnet', 'dns', 'dns_record', 'control_extra', 'target_nonempty', 'container_present', 'deleted_container', 'lock_present', 'later_present', 'malformed'])
def test_live_drift_or_later_control_is_rejected(case: str) -> None:
    reader = FakeReader()
    if case == 'account':
        reader.account['tenantId'] = 'wrong'
    elif case == 'identity':
        reader.gets[handoff.IDENTITY_IDS['job_cleanup'].lower()]['properties']['principalId'] = PRINCIPALS['job_provision']
    elif case == 'fic':
        reader.gets[handoff.FIC_IDS['cleanup'].lower()]['properties']['audiences'] = []
    elif case == 'role':
        first = next((value for key, value in reader.gets.items() if '/roledefinitions/' in key))
        first['properties']['permissions'][0]['actions'] = ['*']
    elif case == 'role_assignment':
        reader.gets[handoff.ASSIGNMENT_IDS['source'].lower()]['properties']['scope'] = handoff.SUBSCRIPTION_SCOPE
    elif case == 'role_condition':
        reader.gets[handoff.ASSIGNMENT_IDS['source'].lower()]['properties'].update(condition='@Resource[unsafe]', conditionVersion='2.0')
    elif case == 'unexpected_grant':
        key = reader._key(handoff._id(handoff.SUBSCRIPTION_SCOPE, 'providers/Microsoft.Authorization/roleAssignments'), handoff.API['authorization'])
        reader.lists[key].append(_resource(handoff._assignment_id(handoff.SUBSCRIPTION_SCOPE, 'unexpected'), {'principalId': '20000000-0000-0000-0000-000000000001', 'principalType': 'ServicePrincipal', 'roleDefinitionId': handoff.ROLE_IDS['marker_create'], 'scope': handoff.SUBSCRIPTION_SCOPE}))
    elif case == 'policy':
        reader.gets[handoff.POLICY_ID.lower()]['properties']['policyRule']['then']['effect'] = 'audit'
    elif case == 'policy_semantics':
        key = reader._key(handoff._id(handoff.TARGET_GROUP, 'providers/Microsoft.Authorization/policyAssignments'), handoff.API['policy'])
        reader.lists[key][1]['properties']['parameters'] = {'effect': {'value': 'Deny'}}
    elif case == 'extra_policy':
        key = reader._key(handoff._id(handoff.TARGET_GROUP, 'providers/Microsoft.Authorization/policyAssignments'), handoff.API['policy'])
        reader.lists[key].append(_resource(handoff._id(handoff.TARGET_GROUP, 'providers/Microsoft.Authorization/policyAssignments/extra'), {'scope': handoff.TARGET_GROUP, 'policyDefinitionId': '/providers/Microsoft.Authorization/policyDefinitions/extra'}))
    elif case == 'storage':
        reader.gets[handoff.STORAGE_ID.lower()]['properties']['publicNetworkAccess'] = 'Enabled'
    elif case == 'storage_pe':
        reader.gets[handoff.STORAGE_ID.lower()]['properties']['privateEndpointConnections'] = [{'id': 'unexpected'}]
    elif case == 'vnet':
        reader.gets[handoff.VNET_ID.lower()]['properties']['addressSpace']['addressPrefixes'] = ['10.0.0.0/8']
    elif case == 'subnet':
        reader.gets[handoff.MARKER_SUBNET_ID.lower()]['properties']['delegations'] = [{'name': 'unsafe'}]
    elif case == 'dns':
        reader.gets[handoff.DNS_LINK_ID.lower()]['properties']['registrationEnabled'] = True
    elif case == 'dns_record':
        key = reader._key(handoff._id(handoff.DNS_ID, 'ALL'), handoff.API['dns'])
        reader.lists[key].append(_resource(handoff._id(handoff.DNS_ID, 'A', 'stale')))
    elif case == 'control_extra':
        key = reader._key(handoff._id(handoff.CONTROL_GROUP, 'resources'), handoff.API['resources'])
        reader.lists[key].append(_resource(handoff._id(handoff.CONTROL_GROUP, 'providers/Microsoft.Compute/virtualMachines/extra')))
    elif case == 'target_nonempty':
        key = reader._key(handoff._id(handoff.TARGET_GROUP, 'resources'), handoff.API['resources'])
        reader.lists[key] = [_resource(handoff._id(handoff.TARGET_GROUP, 'providers/Microsoft.Network/virtualNetworks/extra'))]
    elif case == 'container_present':
        reader.gets[handoff.CONTAINER_IDS[0].lower()] = _resource(handoff.CONTAINER_IDS[0])
    elif case == 'deleted_container':
        key = reader._key(handoff._id(handoff.STORAGE_ID, 'blobServices/default/containers'), handoff.API['storage'], {'$include': 'deleted'})
        reader.lists[key] = [_resource(handoff.CONTAINER_IDS[0], {'deleted': True})]
    elif case == 'lock_present':
        key = reader._key(handoff._id(handoff.STORAGE_ID, 'providers/Microsoft.Authorization/locks'), handoff.API['locks'])
        reader.lists[key] = [_resource(handoff._id(handoff.STORAGE_ID, 'providers/Microsoft.Authorization/locks/unsafe'))]
    elif case == 'malformed':
        reader.gets[handoff.STORAGE_ID.lower()]['sku'] = []
    else:
        reader.gets[handoff.LATER_SUBNET_ID.lower()] = _resource(handoff.LATER_SUBNET_ID)
    with pytest.raises(handoff.HandoffError):
        handoff._capture_snapshot(reader, _attributes())

def test_state_resource_ids_are_exact() -> None:
    state = _valid_state()
    target = next((item for item in state['resources'] if item['type'] == 'azurerm_storage_account'))
    target['instances'][0]['attributes']['id'] = handoff.STORAGE_ID + '-other'
    with pytest.raises(handoff.HandoffError, match='state_resource_id'):
        _attributes(state)
    state = _valid_state()
    role = next(item for item in state['resources'] if item['type'] == 'azurerm_role_definition')
    role['instances'][0]['attributes']['permissions'][0]['actions'] = ['*']
    with pytest.raises(handoff.HandoffError, match='state_role_definition'):
        _attributes(state)

def test_prepare_reruns_validator_and_writes_owner_only_bound_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path.chmod(448)
    state_bytes = json.dumps(_valid_state(), sort_keys=True).encode()
    state_path = tmp_path / 'state.tfstate'
    state_path.write_bytes(state_bytes)
    state_path.chmod(384)
    plan_path = tmp_path / 'recovery.tfplan'
    plan_path.write_bytes(b'plan')
    plan_path.chmod(256)
    execution = tmp_path / 'execution'
    receipt_path = tmp_path / 'handoff.json'
    base = _base_receipt(state_bytes)
    seen: dict[str, Any] = {}

    def verify(**kwargs: Any) -> Mapping[str, Any]:
        seen.update(kwargs)
        execution.mkdir(mode=448)
        return base
    monkeypatch.setattr(handoff.recovery, 'revalidate_recovery_plan', verify)
    pulls = _mock_runtime(monkeypatch, state_bytes, state_bytes)
    receipt = handoff.prepare_handoff(saved_plan=plan_path, state_json=state_path, execution_dir=execution, receipt_path=receipt_path, expected_source_commit='a' * 40, expected_saved_plan_sha256=base['saved_plan_sha256'], reader_factory=FakeReader, clock=lambda: NOW, nonce_source=lambda size: b'n' * size, boot_session=lambda: b'boot-session', source_binding=lambda commit, tree: '8' * 64)
    assert seen == {'saved_plan': plan_path, 'state_json': state_path, 'execution_dir': execution, 'expected_source_commit': 'a' * 40, 'expected_saved_plan_sha256': base['saved_plan_sha256']}
    assert receipt['live_readback_bound'] is True
    assert receipt['apply_authorized'] is False
    assert receipt['base_validation_receipt_sha256'] == handoff._digest(handoff._canonical(base)) and receipt['handoff_source_sha256'] == '8' * 64
    assert receipt['remote_state_sha256'] == handoff._digest(handoff._canonical(json.loads(state_bytes))) and receipt['azure_tls_ca_bundle_sha256'] == 'c' * 64 and len(pulls) == 2
    assert receipt['python_executable_sha256'] == '8' * 64 and receipt['python_runtime_sha256'] == '9' * 64
    directory = execution.stat()
    expected_machine = {'boot': handoff._digest(b'boot-session'), 'euid': os.geteuid(), 'execution_device': directory.st_dev, 'execution_inode': directory.st_ino, 'nonce_sha256': handoff._digest(b'n' * 32)}
    assert receipt['machine_binding_sha256'] == handoff._digest(handoff._canonical(expected_machine))
    assert receipt_path.stat().st_mode & 511 == 256
    assert (execution / '.base-recovery-handoff-nonce').stat().st_mode & 511 == 256
    assert receipt_path.read_bytes() == handoff._canonical(receipt) + b'\n'
    serialized = receipt_path.read_text()
    assert 'operator@example' not in serialized and (not any((value in serialized for value in PRINCIPALS.values())))

@pytest.mark.parametrize('failure', ['base', 'nonce', 'remote', 'stale', 'torn'])
def test_prepare_fail_closed_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    tmp_path.chmod(448)
    state_bytes = json.dumps(_valid_state()).encode()
    state_path = tmp_path / 'state'
    state_path.write_bytes(state_bytes)
    state_path.chmod(384)
    plan_path, execution, receipt_path = (tmp_path / 'plan', tmp_path / 'execution', tmp_path / 'receipt')
    plan_path.write_bytes(b'plan')
    plan_path.chmod(256)
    base = _base_receipt(state_bytes)
    if failure == 'base':
        base['apply_authorized'] = True

    def verify(**_: Any) -> Mapping[str, Any]:
        execution.mkdir(mode=448)
        return base

    class Torn(FakeReader):

        def __init__(self) -> None:
            super().__init__()
            self.storage_reads = 0

        def get(self, resource_id: str, api_version: str, *, missing: bool=False) -> Mapping[str, Any] | None:
            value = super().get(resource_id, api_version, missing=missing)
            if resource_id == handoff.STORAGE_ID and value is not None:
                self.storage_reads += 1
                if self.storage_reads == 2:
                    value = deepcopy(value)
                    value['properties']['provisioningState'] = 'Updating'
            return value
    factory: Any = Torn if failure == 'torn' else (lambda: FakeReader(date=NOW - timedelta(minutes=6))) if failure == 'stale' else FakeReader
    monkeypatch.setattr(handoff.recovery, 'revalidate_recovery_plan', verify)
    changed = json.dumps({**json.loads(state_bytes), 'outputs': {'changed': {'value': True}}}).encode()
    _mock_runtime(monkeypatch, state_bytes, changed if failure == 'remote' else state_bytes)
    with pytest.raises(handoff.HandoffError):
        handoff.prepare_handoff(saved_plan=plan_path, state_json=state_path, execution_dir=execution, receipt_path=receipt_path, expected_source_commit='a' * 40, expected_saved_plan_sha256=base['saved_plan_sha256'], reader_factory=factory, clock=lambda: NOW, nonce_source=(lambda _: b'short') if failure == 'nonce' else lambda size: b'n' * size, boot_session=lambda: b'boot', source_binding=lambda *_: '8' * 64)
    assert not receipt_path.exists()

def test_handoff_source_is_clean_exact_head_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = handoff.recovery.SourceSnapshot('a' * 40, {}, b'lock', 'b' * 64)
    monkeypatch.setattr(handoff.recovery, '_source_snapshot', lambda _: snapshot)
    source = Path(handoff.__file__).read_bytes()
    responses = {'status': b'', 'show': source}
    monkeypatch.setattr(handoff.recovery, '_run_git', lambda arguments: responses[arguments[0]])
    assert handoff._source_binding('a' * 40, 'b' * 64) == handoff._digest(source)
    responses['show'] = b'different'
    with pytest.raises(handoff.HandoffError, match='handoff_source'):
        handoff._source_binding('a' * 40, 'b' * 64)

class _Headers(Message):
    pass

class _Response:

    def __init__(self, url: str, value: Mapping[str, Any], *, extra: int=0) -> None:
        self.status = 200
        self._url = url
        self._raw = json.dumps(value).encode() + b'x' * extra
        self.headers = _Headers()
        self.headers['Date'] = 'Sat, 05 Sep 2026 04:00:00 GMT'
        self.headers['Content-Type'] = 'application/json'
        self.headers['Content-Length'] = str(len(self._raw))

    def geturl(self) -> str:
        return self._url

    def read(self, _size: int) -> bytes:
        return self._raw

    def close(self) -> None:
        pass

class _ResetResponse(_Response):
    def read(self, _size: int) -> bytes:
        raise ConnectionResetError

class _Opener:

    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.responses: list[Any] = []

    def open(self, request: Any, timeout: int) -> _Response:
        assert timeout == 20
        self.requests.append(request)
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

def test_real_transport_pins_cli_get_tls_and_no_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    az = tmp_path / 'az'
    az.write_text('#!/bin/sh\n')
    az.chmod(448)
    commands: list[list[str]] = []
    outputs = [{'id': handoff.SUBSCRIPTION, 'tenantId': handoff.TENANT, 'environmentName': 'AzureCloud', 'state': 'Enabled', 'user': {'type': 'user', 'name': 'operator'}}, {'azure-cli': '2.77.0'}, {'accessToken': 's' * 200, 'tokenType': 'Bearer', 'subscription': handoff.SUBSCRIPTION, 'tenant': handoff.TENANT}]

    def run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        commands.append(arguments)
        assert kwargs['timeout_seconds'] == 60 and kwargs['max_output_bytes'] == 256 * 1024
        return subprocess.CompletedProcess(arguments, 0, json.dumps(outputs.pop(0)).encode(), b'')
    opener = _Opener()
    handlers: list[Any] = []
    monkeypatch.setattr(handoff, '_resolve_azure_cli', lambda: az)
    monkeypatch.setattr(handoff, '_operator_paths', lambda: (tmp_path, tmp_path / '.azure', 'd' * 64))
    monkeypatch.setattr(handoff.recovery, '_executable_sha256', lambda *_, **__: 'a' * 64)
    monkeypatch.setattr(handoff.recovery, '_run_process', run)
    monkeypatch.setattr(handoff, '_hardened_tls_context', lambda: (ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT), 'c' * 64))
    monkeypatch.setattr(handoff.urllib.request, 'build_opener', lambda *values: handlers.extend(values) or opener)
    reader = handoff.AzureManagementReader()
    assert reader.azure_tls_ca_bundle_sha256 == 'c' * 64
    assert reader.azure_config_binding_sha256 == 'd' * 64
    prefix = ['/usr/bin/env', '-i', f'HOME={tmp_path}']
    assert all((command[:3] == prefix and 'HTTPS_PROXY' not in ' '.join(command) and ('PYTHONPATH' not in ' '.join(command)) for command in commands))
    assert all(f'AZURE_CONFIG_DIR={tmp_path / ".azure"}' in command for command in commands)
    assert commands[0][-8:] == [str(az), 'account', 'show', '--subscription', handoff.SUBSCRIPTION, '--only-show-errors', '--output', 'json']
    assert commands[1][-4:] == ['version', '--only-show-errors', '--output', 'json']
    assert commands[2][-10:] == [str(az), 'account', 'get-access-token', '--subscription', handoff.SUBSCRIPTION, '--resource', 'https://management.azure.com/', '--only-show-errors', '--output', 'json']
    assert any((isinstance(value, urllib.request.ProxyHandler) and getattr(value, 'proxies') == {} for value in handlers))
    assert any((isinstance(value, handoff._NoRedirect) for value in handlers))
    url = reader._url(handoff.STORAGE_ID, handoff.API['storage'])
    opener.responses.append(_Response(url, _resource(handoff.STORAGE_ID)))
    result = reader.get(handoff.STORAGE_ID, handoff.API['storage'])
    assert result is not None and result['id'] == handoff.STORAGE_ID
    request = opener.requests[0]
    assert request.get_method() == 'GET' and request.full_url.startswith('https://management.azure.com/subscriptions/')
    assert request.headers['Authorization'] == 'Bearer ' + 's' * 200
    source = inspect.getsource(handoff.AzureManagementReader)
    assert ("method='GET'" in source or 'method="GET"' in source) and all((f"method='{verb}'" not in source and f'method="{verb}"' not in source for verb in ('POST', 'PUT', 'PATCH', 'DELETE')))

def test_get_retries_only_transient_transport_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(handoff.time, 'sleep', sleeps.append)
    def reader_with(*responses: Any) -> tuple[handoff.AzureManagementReader, _Opener, str]:
        reader = handoff.AzureManagementReader.__new__(handoff.AzureManagementReader)
        opener = _Opener()
        reader._token, reader.dates, reader._bytes_read = ('token', [], 0)
        reader._deadline = handoff.time.monotonic() + handoff.MAX_READBACK_SECONDS
        setattr(reader, '_opener', opener)
        url = reader._url(handoff.STORAGE_ID, handoff.API['storage'])
        opener.responses.extend(response(url) if callable(response) else response for response in responses)
        return reader, opener, url
    reader, opener, _ = reader_with(lambda url: _ResetResponse(url, {}), lambda url: _Response(url, _resource(handoff.STORAGE_ID)))
    assert reader.get(handoff.STORAGE_ID, handoff.API['storage']) is not None and len(opener.requests) == 2 and sleeps == [0.05]
    sleeps.clear()
    reader, opener, _ = reader_with(ConnectionResetError(), urllib.error.URLError('reset'), TimeoutError())
    with pytest.raises(handoff.HandoffError, match='arm_request.failed'):
        reader.get(handoff.STORAGE_ID, handoff.API['storage'])
    assert len(opener.requests) == 3 and sleeps == [0.05, 0.1]
    sleeps.clear()
    headers = _Headers()
    headers['Date'] = 'Sat, 05 Sep 2026 04:00:00 GMT'
    reader, opener, url = reader_with(lambda target: urllib.error.HTTPError(target, 503, 'unavailable', headers, None), lambda target: _Response(target, {}))
    with pytest.raises(handoff.HandoffError, match='arm_request.failed'):
        reader.get(handoff.STORAGE_ID, handoff.API['storage'])
    assert url and len(opener.requests) == 1 and sleeps == []

def test_tls_rejects_ambient_trust_or_key_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ('SSLKEYLOGFILE', 'SSL_CERT_FILE', 'SSL_CERT_DIR'):
        monkeypatch.setenv(name, '/tmp/untrusted')
        with pytest.raises(handoff.HandoffError, match='tls.environment'):
            handoff._hardened_tls_context()
        monkeypatch.delenv(name)

def test_cli_and_operator_home_ignore_ambient_path_and_home(monkeypatch: pytest.MonkeyPatch) -> None:
    # pytest's Linux tmp_path lives below root-owned mode-01777 /tmp, which the
    # production ancestry check must reject. Build this positive fixture below
    # the trusted checkout instead; TemporaryDirectory still guarantees cleanup.
    with tempfile.TemporaryDirectory(prefix='.handoff-path-', dir=Path(__file__).resolve().parents[2]) as value:
        tmp_path = Path(value)
        pinned = tmp_path / 'pinned'
        pinned.mkdir(mode=448)
        az = pinned / 'az'
        az.write_text('#!/bin/sh\n')
        az.chmod(448)
        entrypoint = tmp_path / 'az'
        entrypoint.symlink_to(az)
        home = tmp_path / 'home'
        home.mkdir(mode=448)
        (home / '.azure').mkdir(mode=448)
        monkeypatch.setattr(handoff, 'AZURE_CLI_ENTRYPOINTS', (entrypoint,))
        monkeypatch.setattr(handoff.pwd, 'getpwuid', lambda _: type('Passwd', (), {'pw_dir': str(home)})())
        monkeypatch.setenv('PATH', str(tmp_path / 'evil'))
        monkeypatch.setenv('HOME', str(tmp_path / 'evil-home'))
        assert handoff._resolve_azure_cli() == az
        assert handoff._operator_paths()[:2] == (home, home / '.azure')

def test_python_import_and_transport_environment_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert handoff.recovery.__file__ is not None
    assert Path(handoff.recovery.__file__).resolve() == Path(handoff.__file__).with_name('validate_staging_rehearsal_base_recovery_plan.py').resolve()
    assert Path(handoff.__file__).read_text().splitlines()[0] == '#!/opt/homebrew/bin/python3 -IS'
    result = subprocess.run([sys.executable, str(Path(handoff.__file__)), '--help'], check=False, capture_output=True, env={})
    assert result.returncode == 2 and result.stdout == b'' and result.stderr == b'recovery_handoff_refused\n'
    monkeypatch.setenv('PYTHONPATH', '/tmp/shadow')
    with pytest.raises(handoff.HandoffError, match='python_runtime'):
        handoff._python_runtime_binding()
    monkeypatch.delenv('PYTHONPATH')
    monkeypatch.setenv('HTTPS_PROXY', 'https://interceptor.invalid')
    with pytest.raises(handoff.HandoffError, match='transport_environment'):
        handoff._reject_ambient_transport_environment()

def test_transport_rejects_off_host_pagination_and_oversized_response() -> None:
    reader = handoff.AzureManagementReader.__new__(handoff.AzureManagementReader)
    opener = _Opener()
    reader._token, reader.dates = ('token', [])
    reader._deadline = handoff.time.monotonic() + handoff.MAX_READBACK_SECONDS
    reader._bytes_read = 0
    setattr(reader, '_opener', opener)
    with pytest.raises(handoff.HandoffError, match='arm_request'):
        reader._request('https://evil.invalid/subscriptions/x', missing=False)
    url = reader._url(handoff.STORAGE_ID, handoff.API['storage'])
    opener.responses.append(_Response(url, {}, extra=handoff.MAX_ARM_BYTES + 1))
    with pytest.raises(handoff.HandoffError, match='arm_response'):
        reader._request(url, missing=False)
    listing = handoff._id(handoff.CONTROL_GROUP, 'resources')
    first = reader._url(listing, handoff.API['resources'])
    opener.responses.append(_Response(first, {'value': [], 'nextLink': 'https://evil.invalid/next'}))
    with pytest.raises(handoff.HandoffError, match='arm_listing'):
        reader.listing(listing, handoff.API['resources'])

def test_pagination_accepts_documented_token_but_rejects_repeated_or_duplicate_pages() -> None:
    listing = handoff._id(handoff.CONTROL_GROUP, 'resources')
    for repeated in (True, False):
        reader = handoff.AzureManagementReader.__new__(handoff.AzureManagementReader)
        opener = _Opener()
        reader._token, reader.dates, reader._bytes_read = ('token', [], 0)
        reader._deadline = handoff.time.monotonic() + handoff.MAX_READBACK_SECONDS
        setattr(reader, '_opener', opener)
        first = reader._url(listing, handoff.API['resources'])
        second = first + '&%24skipToken=opaque'
        opener.responses.append(_Response(first, {'value': [_resource(handoff.STORAGE_ID)], 'nextLink': second}))
        opener.responses.append(_Response(second, {'value': [_resource(handoff.VNET_ID if repeated else handoff.STORAGE_ID)], **({'nextLink': second} if repeated else {})}))
        with pytest.raises(handoff.HandoffError, match='arm_listing'):
            reader.listing(listing, handoff.API['resources'])

def test_query_allowlist_permits_only_exact_deleted_container_inventory() -> None:
    reader = handoff.AzureManagementReader.__new__(handoff.AzureManagementReader)
    containers = handoff._id(handoff.STORAGE_ID, 'blobServices/default/containers')
    url = reader._url(containers, handoff.API['storage'], {'$include': 'deleted'})
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(url).query) == {'api-version': [handoff.API['storage']], '$include': ['deleted']}
    for resource_id, api, query in ((containers, handoff.API['storage'], {'$include': 'active'}), (containers, handoff.API['storage'], {'include': 'deleted'}), (containers, handoff.API['storage'], {'$include': 'deleted', '$filter': 'x'}), (handoff.STORAGE_ID, handoff.API['storage'], {'$include': 'deleted'}), (containers, handoff.API['network'], {'$include': 'deleted'})):
        with pytest.raises(handoff.HandoffError, match='arm_request'):
            reader._url(resource_id, api, query)

def test_cli_emits_only_receipt_or_bounded_refusal(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(handoff, 'prepare_handoff', lambda **_: {'pass': True, 'live_readback_bound': True, 'apply_authorized': False})
    args = ['--saved-plan', '/p/plan', '--state-json', '/p/state', '--execution-dir', '/p/execution', '--receipt', '/p/receipt', '--expected-source-commit', 'a' * 40, '--expected-saved-plan-sha256', 'b' * 64]
    assert handoff.main(args) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)['live_readback_bound'] is True and output.err == ''
    monkeypatch.setattr(handoff, 'prepare_handoff', lambda **_: handoff._fail('secret response text'))
    assert handoff.main(args) == 2
    output = capsys.readouterr()
    assert output.out == '' and output.err == 'recovery_handoff_refused\n' and ('secret' not in output.err)

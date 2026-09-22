"""Empty broker identity edit detectors; not live IAM or runtime attestation."""

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "infra-staging-rehearsal"
EXISTING_IDENTITY_SHA256 = (
    "57e0842c734f7c789e9ed27fd7afe7b0325805fb51ef9848c6aa20eec8d4dff3"
)


def _sources() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in ROOT.glob("*.tf")}


def _assert_identity_boundary(sources: dict[str, str]) -> None:
    # Exact root-file/declaration edit detection, not a general HCL or transitive
    # expression proof. Existing role/AzAPI snapshots are checked separately.
    assert (
        hashlib.sha256(sources["identity.tf"].encode()).hexdigest()
        == EXISTING_IDENTITY_SHA256
    )
    declarations = []
    for filename, source in sources.items():
        for kind, resource_type, name in re.findall(
            r'^(resource|data) "([^"]+)" "([^"]+)"', source, re.MULTILINE
        ):
            if (
                resource_type == "azurerm_user_assigned_identity"
                or "federated_identity_credential" in resource_type
            ):
                declarations.append((filename, kind, resource_type, name))
    assert len(declarations) == 4
    assert set(declarations) == {
        ("identity.tf", "resource", "azurerm_user_assigned_identity", "dispatcher"),
        ("identity.tf", "resource", "azurerm_user_assigned_identity", "job"),
        (
            "identity.tf",
            "resource",
            "azurerm_federated_identity_credential",
            "dispatcher",
        ),
        (
            "broker-identities.tf",
            "resource",
            "azurerm_user_assigned_identity",
            "private_broker",
        ),
    }


def test_existing_iam_and_federation_remain_exact() -> None:
    _assert_identity_boundary(_sources())


def test_broker_source_has_only_one_gated_keyed_identity_declaration() -> None:
    source = (ROOT / "broker-identities.tf").read_text(encoding="utf-8")
    assert re.findall(
        r'^(resource|data) "([^"]+)" "([^"]+)"', source, re.MULTILINE
    ) == [("resource", "azurerm_user_assigned_identity", "private_broker")]
    assert re.search(
        r"^\s*for_each\s*=\s*local\.private_broker_identities_enabled\s*\?\s*local\.restore_broker_identities\s*:\s*\{\}$",
        source,
        re.MULTILINE,
    )
    assert re.search(r"^\s*name\s*=\s*each\.value\.name$", source, re.MULTILINE)
    assert re.search(r"^\s*prevent_destroy\s*=\s*true$", source, re.MULTILINE)
    assert not re.search(r"^\s*(provisioner|connection)\s*\{", source, re.MULTILINE)


def test_broker_enablement_and_exact_plan_approval_default_off() -> None:
    variables = (ROOT / "variables.tf").read_text(encoding="utf-8")
    for name in (
        "enable_private_restore_broker_identities",
        "private_restore_broker_identity_plan_approved",
    ):
        match = re.search(rf'variable "{name}" \{{(.*?)\n\}}', variables, re.DOTALL)
        assert match is not None
        assert re.search(r"^\s*default\s*=\s*false$", match[1], re.MULTILINE)
    source = (ROOT / "broker-identities.tf").read_text(encoding="utf-8")
    assert re.search(
        r"private_broker_identities_enabled\s*=\s*\(\s*"
        r"var\.deploy_staging_restore_control_plane\s*&&\s*"
        r"var\.enable_private_restore_broker_identities\s*\)",
        source,
    )


@pytest.mark.parametrize(
    "extra",
    [
        'resource "azurerm_federated_identity_credential" "broker" {\n user_assigned_identity_id = local.indirect_broker\n}\n',
        'resource "azuread_application_federated_identity_credential" "broker" {\n}\n',
        'data "azurerm_user_assigned_identity" "other_broker" {\n}\n',
    ],
)
def test_added_identity_or_federation_surface_is_rejected(extra: str) -> None:
    sources = _sources()
    sources["later.tf"] = extra
    with pytest.raises(AssertionError):
        _assert_identity_boundary(sources)


def test_existing_dispatcher_federation_cannot_be_redirected_silently() -> None:
    sources = _sources()
    before = sources["identity.tf"]
    sources["identity.tf"] = before.replace(
        "azurerm_user_assigned_identity.dispatcher[each.key].id",
        "azurerm_user_assigned_identity.private_broker[each.key].id",
    )
    assert sources["identity.tf"] != before
    with pytest.raises(AssertionError):
        _assert_identity_boundary(sources)

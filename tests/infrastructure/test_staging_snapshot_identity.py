"""Invented identities/JWT-shaped strings only; no real token, cache or network."""

import base64
import copy
import importlib.util
import json
import sys
import unittest
import uuid
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "staging_snapshot_identity",
    Path(__file__).resolve().parents[2] / "scripts/staging_snapshot_identity.py",
)
assert SPEC is not None and SPEC.loader is not None
identity = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = identity
SPEC.loader.exec_module(identity)

NOW = 1900000000
SUB = str(uuid.UUID(int=1))
TENANT = str(uuid.UUID(int=2))
OID = str(uuid.UUID(int=3))
HOME_ID = "invented.home-account"


def encoded(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.account = {
            "id": SUB,
            "tenantId": TENANT,
            "state": "Enabled",
            "environmentName": "AzureCloud",
            "user": {"type": "user", "name": "invented-user"},
        }
        self.claims = {
            "tid": TENANT,
            "oid": OID,
            "iss": f"https://sts.windows.net/{TENANT}/",
            "aud": "https://management.azure.com/",
            "exp": NOW + 3600,
            "nbf": NOW - 60,
        }
        self.expected = identity.ExpectedIdentity(
            SUB, TENANT, identity.digest("invented-user")
        )
        self.reset_token()

    def reset_token(self):
        self.token = (
            encoded({"alg": "not-real"}) + "." + encoded(self.claims) + "." + "x" * 80
        )
        self.cache: dict = {
            "Account": {
                "fixture": {"username": "INVENTED-USER", "home_account_id": HOME_ID}
            },
            "AccessToken": {
                "fixture": {
                    "secret": self.token,
                    "home_account_id": HOME_ID,
                    "realm": TENANT,
                    "credential_type": "AccessToken",
                    "client_id": identity.CLI_CLIENT_ID,
                }
            },
        }

    def bind(self, raw=None):
        return identity.bind_configured_identity(
            self.account,
            self.token,
            raw or json.dumps(self.cache).encode(),
            self.expected,
            now_epoch=NOW,
            minimum_remaining_seconds=1800,
        )

    def refused(self):
        with self.assertRaisesRegex(
            identity.IdentityRefused, "^snapshot_identity_linkage_refused$"
        ):
            self.bind()

    def test_hash_only_linkage_is_not_signature_or_execution_authority(self):
        result = self.bind()
        self.assertTrue(result["configured_home_account_linked_to_exact_arm_token"])
        self.assertFalse(result["jwt_signature_independently_verified"])
        self.assertFalse(result["human_apply_authorized"])
        self.assertTrue(result["authenticated_same_token_read_required"])
        for private in ("invented-user", HOME_ID, OID, self.token):
            self.assertNotIn(private, str(result))

    def test_wrong_account_scope_kind_user_and_cloud_refuse(self):
        for path, value in (
            ("id", TENANT),
            ("tenantId", SUB),
            ("state", "Disabled"),
            ("environmentName", "Other"),
            ("user", {"type": "servicePrincipal"}),
            ("user", {"type": "user", "name": "other"}),
        ):
            self.setUp()
            self.account[path] = value
            self.refused()

    def test_claim_scope_issuer_audience_time_and_uuid_refuse(self):
        for key, value in (
            ("tid", SUB),
            ("oid", "invalid"),
            ("iss", "https://other/"),
            ("aud", "https://graph.microsoft.com/"),
            ("exp", NOW + 1800),
            ("exp", True),
            ("nbf", NOW + 31),
            ("nbf", False),
        ):
            self.setUp()
            self.claims[key] = value
            self.reset_token()
            self.refused()

    def test_exact_token_cache_record_must_match_all_linkage_fields(self):
        for key, value in (
            ("secret", "different-token"),
            ("home_account_id", "other"),
            ("realm", SUB),
            ("credential_type", "RefreshToken"),
            ("client_id", "other"),
        ):
            self.setUp()
            self.cache["AccessToken"]["fixture"][key] = value
            self.refused()

    def test_multiple_home_accounts_or_inconsistent_token_alias_refuse(self):
        self.cache["Account"]["second"] = {
            "username": "invented-user",
            "home_account_id": "other",
        }
        self.refused()
        self.setUp()
        self.cache["AccessToken"]["second"] = copy.deepcopy(
            self.cache["AccessToken"]["fixture"]
        )
        self.cache["AccessToken"]["second"]["home_account_id"] = "other"
        self.refused()

    def test_missing_empty_and_malformed_cache_sections_refuse(self):
        for section in ("Account", "AccessToken"):
            for value in ({}, [], None):
                self.setUp()
                self.cache[section] = value
                self.refused()

    def test_malformed_or_duplicate_json_never_leaks_content(self):
        for raw in (
            b'{"private":"invented-secret",',
            b'{"Account":{},"Account":{}}',
            b'{"Account":NaN}',
            b"[]",
        ):
            with self.assertRaisesRegex(
                identity.IdentityRefused, "^snapshot_identity_linkage_refused$"
            ):
                self.bind(raw)

    def test_duplicate_token_claims_refuse(self):
        raw = b'{"tid":"invented","tid":"duplicate"}'
        self.token = (
            "header."
            + base64.urlsafe_b64encode(raw).decode().rstrip("=")
            + "."
            + "x" * 80
        )
        self.refused()


if __name__ == "__main__":
    unittest.main()

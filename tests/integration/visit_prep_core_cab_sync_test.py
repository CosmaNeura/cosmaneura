"""Reuse actual upstream CAB authorization and wire cases in the owned Core DB."""

from tests.billing_header import bridge_authority_test, bridge_bff_test
from tests.billing_header.operations_test import case as case
from tests.integration.visit_prep_core_sync_test import (
    billing_database as billing_database,
)

bridge = bridge_bff_test.bridge
# Reexport rather than duplicate the operational feature's existing test logic.
# All HTTP/model responses are synthetic; the owned plugin denies network.
for suite in (bridge_authority_test, bridge_bff_test):
    for name, test in vars(suite).items():
        if name.startswith("test_"):
            globals()[name] = test

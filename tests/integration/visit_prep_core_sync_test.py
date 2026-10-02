"""Run the actual Core billing source cases on the owned, fully migrated DB."""

import pytest

from activemodel.session_manager import get_engine
from sqlalchemy import text

from tests.billing_header.operations_test import case as case
from tests.billing_header.operations_test import (
    test_actual_expand_prepare_exact_replay_and_immutable_source as test_actual_expand_prepare_exact_replay_and_immutable_source,
)
from tests.billing_header.operations_test import (
    test_admission_revocation_before_commit_rolls_back_operation as test_admission_revocation_before_commit_rolls_back_operation,
)
from tests.billing_header.operations_test import (
    test_changed_context_uuid_conflict_and_explicit_new_revision as test_changed_context_uuid_conflict_and_explicit_new_revision,
)
from tests.billing_header.operations_test import (
    test_changed_or_superseded_note_rejects_old_retry as test_changed_or_superseded_note_rejects_old_retry,
)
from tests.billing_header.operations_test import (
    test_current_actor_member_and_note_are_rechecked as test_current_actor_member_and_note_are_rechecked,
)
from tests.billing_header.operations_test import (
    test_map_uses_exact_displayed_ordered_snapshot_and_does_not_refresh_it as test_map_uses_exact_displayed_ordered_snapshot_and_does_not_refresh_it,
)
from tests.billing_header.operations_test import (
    test_patient_reassignment_denies_old_receipt_and_capability as test_patient_reassignment_denies_old_receipt_and_capability,
)
from tests.billing_header.operations_test import (
    test_real_routes_current_authorization_and_no_store_receipt as test_real_routes_current_authorization_and_no_store_receipt,
)
from tests.visit_prep.isolated_plugin import _owned_test_root


@pytest.fixture
def billing_database():
    root = _owned_test_root()
    engine = get_engine()
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT current_setting('data_directory')")
        ).scalar_one() == str(root / "data")
        assert connection.execute(
            text("SELECT to_regclass('billing_header_prepared_operation')")
        ).scalar_one()
        assert connection.execute(
            text("SELECT to_regclass('visit_prep_booking_receipt')")
        ).scalar_one()
    # No replacement engine, create_all, truncate, configured service or cleanup.
    yield engine

"""Run the reviewed additive repair only inside the fixed staging Core app.

Default: a read-only plan, rolled back. Apply requires its exact digest and
separate owner-directed membership confirmation. No credentials are printed.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import cast

from typeid import TypeID

from sqlalchemy.engine import make_url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--additional-doctor-id", required=True)
    parser.add_argument("--capture-binding-id", required=True)
    parser.add_argument("--operator-ref", required=True)
    parser.add_argument("--confirm-plan-sha256")
    parser.add_argument(
        "--confirm-owner-directed-pilot-membership", action="store_true"
    )
    parser.add_argument("--preserve-solo-enrollment", action="store_true")
    arguments = parser.parse_args()
    try:
        url = make_url(os.environ["DATABASE_URL"])
        if (
            os.environ.get("PYTHON_ENV") != "staging"
            or os.environ.get("CONTAINER_APP_NAME") != "cosmaneura-staging-app"
            or url.host != "cosmaneura-staging-postgres.postgres.database.azure.com"
            or url.database != "staging"
            or not arguments.confirm_owner_directed_pilot_membership
        ):
            raise ValueError
        doctor_id = TypeID.from_string(arguments.additional_doctor_id)
        binding_id = TypeID.from_string(arguments.capture_binding_id)
        if (
            doctor_id.prefix != "doctor"
            or binding_id.prefix != "scribe_capture_binding"
        ):
            raise ValueError

        from app.services.staging_scribe_prep_reconciliation import reconcile

        from activemodel.session_manager import global_session
        from activemodel.types import TypeIDType

        with global_session() as session:
            session.connection(execution_options={"isolation_level": "SERIALIZABLE"})
            result = reconcile(
                session,
                additional_doctor_id=cast(TypeIDType, doctor_id),
                capture_binding_id=cast(TypeIDType, binding_id),
                operator_ref=arguments.operator_ref,
                preserve_solo_enrollment=arguments.preserve_solo_enrollment,
                confirm_plan_sha256=arguments.confirm_plan_sha256,
            )
            if arguments.confirm_plan_sha256:
                session.commit()
            else:
                session.rollback()
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:  # noqa: BLE001 -- operator output must not contain private data
        print(json.dumps({"status": "refused_or_unconfirmed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

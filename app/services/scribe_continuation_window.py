"""Validate a combined reply with the same ordinary lease/reservation rules."""

from typing import Literal

from pydantic import BaseModel

from app.services import scribe_capture_protocol as protocol
from app.services.scribe_capture_admission import AdmittedCaptureScope
from app.services.scribe_continuation_window_contract import PrepareContinuationWindow


class PreparedContinuationWindow(protocol.ProtectedContract):
    schema_version: Literal["1.0"] = "1.0"
    lease: protocol.LeaseReceipt
    reservations: protocol.ReservationReceipt


def validate_prepared_continuation_window(
    operation: PrepareContinuationWindow,
    receipt: BaseModel,
    scope: AdmittedCaptureScope,
) -> PreparedContinuationWindow:
    from app.services.scribe_capture_gateway import (
        CaptureReceiptInvalid,
        validate_receipt,
    )

    try:
        result = PreparedContinuationWindow.model_validate(
            receipt.model_dump(mode="json")
        )
        body = operation.payload
        count = len(result.reservations.reservations)
        renew = protocol.RenewLease(
            kind="renew_lease",
            lease_id=body.previous_lease_id,
            payload=protocol.RenewPayload(
                expected_generation=body.previous_lease_generation
            ),
        )
        reserve = protocol.ReserveChunks(
            kind="reserve_chunks",
            payload=protocol.ReservePayload(
                capture_lease_id=result.lease.lease_id,
                capture_lease_generation=result.lease.generation,
                channel=body.channel,
                first_sequence_number=body.first_sequence_number,
                count=count,
            ),
        )
        validate_receipt(renew, result.lease, scope, upload_origins=())
        validate_receipt(reserve, result.reservations, scope, upload_origins=())
        if result.reservations.capture_authorized_until != result.lease.expires_at:
            raise ValueError
        return result
    except (ValueError, TypeError, AttributeError):
        raise CaptureReceiptInvalid from None

"""Value-free staging diagnostics for otherwise indistinguishable Scribe 503s."""

import structlog

from app.environments import is_staging

_ERRORS = frozenset(
    {
        "AdmissionError",
        "AdmissionUnconfirmed",
        "CaptureOperationUnconfirmed",
        "RecordUnavailable",
        "RecordDeadline",
        "SQLAlchemyError",
        "OperationalError",
        "InterfaceError",
        "IntegrityError",
        "ProgrammingError",
        "DBAPIError",
        "LockNotAvailable",
        "QueryCanceled",
        "DeadlockDetected",
        "SerializationFailure",
        "ConnectionFailure",
        "ConnectionException",
        "TooManyConnections",
        "EncryptionError",
        "DecryptionError",
        "ValueError",
        "TypeError",
        "TimeoutError",
        "ReadTimeout",
        "ConnectTimeout",
        "PoolTimeout",
        "HTTPStatusError",
        "ScribeWorkflowUnavailable",
        "ScribeWorkflowError",
    }
)
_STATES = frozenset(
    {
        "55P03",
        "57014",
        "40001",
        "40P01",
        "08006",
        "08003",
        "53300",
        "53200",
        "57P01",
        "23505",
        "25P02",
        "25006",
    }
)
_MODULES = frozenset(
    {
        "app.services.scribe_capture_gateway",
        "app.services.scribe_capture_admission",
        "app.services.scribe_capture_scope",
        "app.services.scribe_capture_deadline",
        "app.services.scribe_encounters",
        "app.services.scribe_workflow",
        "app.services.companion_records",
        "app.services.companion_encounters",
        "app.services.companion_appointment_setup",
        "app.services.doctor_access",
        "app.services.doctor_access_row_ownership",
        "app.services.provider_record_authority",
    }
)
_OPERATIONS = frozenset(
    {
        "list",
        "lookup",
        "create",
        "patients",
        "reserve_chunks",
        "authorize_upload",
        "chunk_receipt",
        "register_chunk",
        "complete",
        "issue_lease",
        "renew_lease",
        "capture_lease_state",
        "capture_recovery_state",
        "provider_encounter",
    }
)


def report_unconfirmed(*, boundary: str, operation: str, error: Exception) -> None:
    """Never log messages, locals, SQL, parameters, IDs, headers or request bodies.

    Existing request logging supplies correlation. Diagnostics must not replace
    the route's original error response, even if the logger itself fails.
    """
    try:
        if not is_staging() or boundary not in {"encounter", "capture_operation"}:
            return
        failures = []
        seen: set[int] = set()
        current: BaseException | None = error
        while current is not None and len(failures) < 4 and id(current) not in seen:
            seen.add(id(current))
            name = type(current).__name__
            code = getattr(current, "sqlstate", None)
            sites = []
            frame = current.__traceback__
            for _ in range(64):
                if frame is None:
                    break
                module = frame.tb_frame.f_globals.get("__name__")
                if module in _MODULES:
                    sites.append({"module": module, "line": frame.tb_lineno})
                frame = frame.tb_next
            failures.append(
                {
                    "error_type": name if name in _ERRORS else "other",
                    "sqlstate": code
                    if isinstance(code, str) and code in _STATES
                    else None,
                    "sites": sites[-4:],
                }
            )
            following = current.__cause__ or current.__context__
            if following is None:
                original = getattr(current, "orig", None)
                following = original if isinstance(original, BaseException) else None
            current = following
        structlog.get_logger(__name__).warning(
            "scribe_operation_unconfirmed",
            boundary=boundary,
            operation=operation if operation in _OPERATIONS else "other",
            failures=failures,
        )
    except Exception:  # noqa: BLE001, S110 -- preserve original response on diagnostic failure
        pass

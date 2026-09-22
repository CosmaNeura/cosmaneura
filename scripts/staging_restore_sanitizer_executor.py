#!/usr/bin/env python3
"""Dormant trusted-evidence preflight for the restore sanitizer executor.

The only public data input is an exact sealed in-process evidence authority.
After current-time ancestry authentication this module still refuses before
any external interaction or sanitizer execution can occur.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import NoReturn

try:
    from scripts import (
        staging_restore_sanitizer_evidence_authority as evidence_authority,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` import compatibility.
    import staging_restore_sanitizer_evidence_authority as evidence_authority


ACTIVATION_FAILURE = "activation.data_contract_executor_unimplemented"
_FAILURE_CODES = frozenset(
    {
        ACTIVATION_FAILURE,
        "sanitizer_executor.authority_invalid",
        "sanitizer_executor.clock_invalid",
        "sanitizer_executor.clock_regressed",
        "sanitizer_executor.configuration_invalid",
        "sanitizer_executor.policy_invalid",
        "sanitizer_executor.target_expired",
    }
)
_EVIDENCE_FAILURE_MAP = {
    "sanitizer_evidence.clock_invalid": "sanitizer_executor.clock_invalid",
    "sanitizer_evidence.clock_regressed": "sanitizer_executor.clock_regressed",
    "sanitizer_evidence.policy_invalid": "sanitizer_executor.policy_invalid",
    "sanitizer_evidence.target_expired": "sanitizer_executor.target_expired",
}


class SanitizerExecutorFailure(RuntimeError):
    """A bounded refusal that never includes retained authority contents."""


def _fail(code: str) -> NoReturn:
    raise SanitizerExecutorFailure(code)


def _trusted_utc(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail("sanitizer_executor.clock_invalid")
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
        or value.microsecond != 0
    ):
        _fail("sanitizer_executor.clock_invalid")
    return value.astimezone(UTC)


class SanitizerExecutorPreflight:
    """Closed-world preflight configured only by a reviewed private clock."""

    __slots__ = ("_clock", "_last")

    def __init__(self) -> None:
        _fail("sanitizer_executor.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls, *, clock: Callable[[], datetime]
    ) -> SanitizerExecutorPreflight:
        if cls is not SanitizerExecutorPreflight or not callable(clock):
            _fail("sanitizer_executor.configuration_invalid")
        value = object.__new__(cls)
        value._clock = clock
        value._last = None
        return value

    def _read_clock(self) -> datetime:
        now = _trusted_utc(self._clock)
        if self._last is not None and now < self._last:
            _fail("sanitizer_executor.clock_regressed")
        self._last = now
        return now

    def preflight(
        self,
        *,
        authenticated_sanitizer_evidence: (
            evidence_authority.AuthenticatedSanitizerEvidence
        ),
    ) -> NoReturn:
        """Reauthenticate the evidence at current time, then refuse activation."""

        try:
            if type(self) is not SanitizerExecutorPreflight:
                _fail("sanitizer_executor.configuration_invalid")
            started_at = self._read_clock()
            evidence_authority._reauthenticate_at_current_time(  # noqa: SLF001
                authenticated_sanitizer_evidence,
                now=started_at,
            )
            completed_at = self._read_clock()
            if completed_at != started_at:
                evidence_authority._reauthenticate_at_current_time(  # noqa: SLF001
                    authenticated_sanitizer_evidence,
                    now=completed_at,
                )
        except Exception as error:
            if (
                type(error) is SanitizerExecutorFailure
                and len(error.args) == 1
                and type(error.args[0]) is str
                and error.args[0] in _FAILURE_CODES
            ):
                code = error.args[0]
            elif (
                type(error) is evidence_authority.SanitizerEvidenceFailure
                and len(error.args) == 1
                and type(error.args[0]) is str
            ):
                code = _EVIDENCE_FAILURE_MAP.get(
                    error.args[0], "sanitizer_executor.authority_invalid"
                )
            else:
                code = "sanitizer_executor.authority_invalid"
            raise SanitizerExecutorFailure(code) from None
        _fail(ACTIVATION_FAILURE)

import os

import pytest

from app.jobs.anonymize import deidentify

pytestmark = pytest.mark.skipif(
    not (
        os.getenv("RUN_LIVE_DEIDENTIFICATION_TESTS") == "true"
        and all(
            os.getenv(env_var)
            for env_var in [
                "AZURE_DEID_TENANT_ID",
                "AZURE_DEID_CLIENT_ID",
                "AZURE_DEID_CLIENT_SECRET",
                "AZURE_DEID_ENDPOINT",
            ]
        )
    ),
    reason=(
        "requires Azure de-identification access; set RUN_LIVE_DEIDENTIFICATION_TESTS=true "
        "and Azure de-identification credentials"
    ),
)


def test_anonymize():
    deid_string = deidentify(
        "John Smith had bypass surgery in St. Francis hospital on June 12, 2013."
    )

    assert "John" not in deid_string
    assert "Smith" not in deid_string
    assert "St. Francis" not in deid_string
    assert "June 12" not in deid_string

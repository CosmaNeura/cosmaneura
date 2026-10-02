"""Real current Core sources and storage; invented, networkless AI providers."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest
from structlog.testing import capture_logs
from typeid import TypeID

import app.services.visit_prep_protected_analysis as protection
import app.services.visit_prep_synthesis_worker as worker
from app.services.visit_prep_contributions import explain_contribution
from app.services.visit_prep_interviews import report_wrong_recipient
from app.services.visit_prep_protected_analysis import (
    PROTECTION_PROFILE,
    VisitPrepProtectionFailed,
    protect_patient_analysis,
    validate_protected_analysis,
    validate_protected_brief,
)
from app.services.visit_prep_synthesis_contract import VisitPrepSynthesisInvalid
from app.services.visit_prep_synthesis_generator import (
    VisitPrepGenerationFailed,
    generate_patient_brief,
)
from app.services.visit_prep_synthesis_payload import (
    decrypt_synthesis_payload,
    encrypt_synthesis_payload,
)
from app.services.visit_prep_synthesis_worker import run_one_build
from app.utils.encryption import decrypt_value, encrypt_value

from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_synthesis_builds_test import (
    _build,
    _change_build,
    _expire,
    _request,
)
from tests.services.visit_prep_synthesis_publication_test import _rows, _selected, _view
from tests.services.visit_prep_synthesis_test import _brief, _fake, _item
from tests.services.visit_prep_synthesis_usage_test import _events
from tests.services.visit_preps_test import _withdraw_seat


def _deid(monkeypatch, transform=lambda body: body):
    captured = []

    def deidentify(body, **options):
        captured.append((body, options))
        return transform(body)

    monkeypatch.setattr(protection, "should_use_test_openai_fallback", lambda: False)
    monkeypatch.setattr(protection, "deidentify", deidentify)
    monkeypatch.setattr(worker, "protect_patient_analysis", protect_patient_analysis)
    return captured


def _sources(monkeypatch):
    fixture = _prepared(monkeypatch, "protected-analysis")
    _correct(fixture, answer="Fictional_Name_Alpha reports symptoms for 3 days.")
    original = _selected(fixture)
    # Authored physician wording and choice labels are also untrusted PHI input.
    first = replace(
        original.sources[0], prompt="Fictional_Doctor_Beta: describe your concern."
    )
    return fixture, replace(original, sources=(first, *original.sources[1:]))


def test_current_text_only_is_deidentified_once_with_exact_source_lineage(monkeypatch):
    fixture, original = _sources(monkeypatch)
    captured = _deid(
        monkeypatch,
        lambda body: body.replace("Fictional_Name_Alpha", "Person_Replacement").replace(
            "Fictional_Doctor_Beta", "Provider_Replacement"
        ),
    )
    protected = protect_patient_analysis(original)
    assert len(captured) == 1
    body, options = captured[0]
    assert options == {"timeout_seconds": 12.0}
    assert "Fictional_Name_Alpha" in body and "Fictional_Doctor_Beta" in body
    assert "My invented health story." not in body
    assert fixture.access_token not in body and str(fixture.prep_id) not in body
    assert all(source.source_ref not in body for source in original.sources)
    assert original.sources[0].source_ref == protected.selection.sources[0].source_ref
    assert original.source_version == protected.selection.source_version
    assert "Fictional_Name_Alpha" not in repr(protected)
    assert "Fictional_Name_Alpha" not in protected.selection.sources[-1].answer
    assert "Fictional_Doctor_Beta" not in protected.selection.sources[0].prompt
    choice = next(source for source in original.sources if source.kind == "choice")
    transformed = next(
        source for source in protected.selection.sources if source.kind == "choice"
    )
    assert transformed.answer == choice.answer
    assert choice.choice_label in body
    validate_protected_analysis(protected, original)


@pytest.mark.parametrize(
    "failure", ["sdk", "empty", "framing", "extra", "overflow", "timeout"]
)
def test_deidentification_failure_never_falls_back_or_exposes_provider_text(
    monkeypatch, failure
):
    _, original = _sources(monkeypatch)

    def broken(body):
        if failure == "sdk":
            raise RuntimeError("Fictional_Name_Alpha secret provider payload")
        return {
            "empty": "",
            "framing": body.replace("_BEGIN", "_BROKEN"),
            "extra": body + "unexpected",
            "overflow": "x" * 20001,
            "timeout": body,
        }[failure]

    _deid(monkeypatch, broken)
    if failure == "timeout":
        ticks = iter((0.0, 12.0))
        monkeypatch.setattr(protection, "monotonic", lambda: next(ticks))
    with capture_logs() as logs, pytest.raises(VisitPrepProtectionFailed) as error:
        protect_patient_analysis(original)
    assert str(error.value) == "Visit Prep protected analysis is unavailable"
    assert error.value.__cause__ is None and error.value.__suppress_context__
    assert "Fictional_Name_Alpha" not in json.dumps(logs)


def test_test_environment_denies_deidentification_before_provider_request(monkeypatch):
    _, original = _sources(monkeypatch)
    captured = _deid(monkeypatch)
    monkeypatch.setattr(protection, "should_use_test_openai_fallback", lambda: True)
    with pytest.raises(VisitPrepProtectionFailed):
        protect_patient_analysis(original)
    assert captured == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_version", "f" * 64),
        ("actor_role", "caregiver"),
        ("package_id", str(TypeID("visit_prep"))),
    ],
)
def test_protected_input_cannot_rebind_original_authority_or_source(
    monkeypatch, field, value
):
    _, original = _sources(monkeypatch)
    _deid(monkeypatch)
    protected = protect_patient_analysis(original)
    with pytest.raises(VisitPrepSynthesisInvalid):
        validate_protected_analysis(
            replace(
                protected, selection=replace(protected.selection, **{field: value})
            ),
            original,
        )


def test_model_receives_only_protected_text_and_cites_unchanged_patient_words(
    monkeypatch,
):
    _, original = _sources(monkeypatch)
    expected = _brief(original).model_copy(
        update={
            "summary": [],
            "headline": _item(
                original.sources[-1],
                text="Recipient reports symptoms for 3 days.",
                quote="symptoms for 3 days.",
            ),
            "visit_priorities": [],
        }
    )
    expected = expected.model_copy(update={"summary": [expected.headline]})
    model = _fake(monkeypatch, expected.model_dump_json())
    _deid(
        monkeypatch,
        lambda body: body.replace("Fictional_Name_Alpha", "Person_Replacement").replace(
            "Fictional_Doctor_Beta", "Provider_Replacement"
        ),
    )
    protected = protect_patient_analysis(original)
    usage = []
    result = generate_patient_brief(
        original, protected=protected, record_usage=usage.append
    )
    assert result == expected and usage[0].status == "succeeded"
    request = json.dumps(model["request"])
    assert (
        "Fictional_Name_Alpha" not in request and "Fictional_Doctor_Beta" not in request
    )
    assert "Person_Replacement" in request and "Provider_Replacement" in request
    encrypted = encrypt_synthesis_payload(result, original, protected=protected)
    assert "Fictional_Name_Alpha" not in encrypted
    stored = json.loads(decrypt_value(encrypted))
    assert stored["protection"]["profile"] == PROTECTION_PROFILE
    assert (
        stored["protection"]["sources"][-1]["reported_text"]
        == protected.selection.sources[-1].answer
    )
    assert decrypt_synthesis_payload(encrypted, original) == expected


def test_surrogate_quotes_cannot_be_published_as_original_patient_words(monkeypatch):
    _, original = _sources(monkeypatch)
    _deid(
        monkeypatch,
        lambda body: body.replace("Fictional_Name_Alpha", "Person_Replacement"),
    )
    protected = protect_patient_analysis(original)
    model = _fake(monkeypatch, _brief(protected.selection).model_dump_json())
    with pytest.raises(VisitPrepGenerationFailed):
        generate_patient_brief(
            original, protected=protected, record_usage=lambda _: None
        )
    assert model["request"]


@pytest.mark.parametrize(
    "surrogate", ["Person_Replacement", "September 30", "Person Replacement"]
)
def test_surrogate_narrative_with_unchanged_citation_is_rejected_at_all_boundaries(
    monkeypatch, surrogate
):
    _, original = _sources(monkeypatch)
    _deid(monkeypatch, lambda body: body.replace("Fictional_Name_Alpha", surrogate))
    protected = protect_patient_analysis(original)
    item = _item(
        original.sources[-1],
        text=f"Recipient {surrogate.split()[0]} reports symptoms for 3 days.",
        quote="symptoms for 3 days.",
    )
    prepared = _brief(original).model_copy(update={"headline": item, "summary": [item]})
    with pytest.raises(VisitPrepSynthesisInvalid):
        validate_protected_brief(prepared, protected, original)
    with pytest.raises(VisitPrepSynthesisInvalid):
        encrypt_synthesis_payload(prepared, original, protected=protected)
    _fake(monkeypatch, prepared.model_dump_json())
    with pytest.raises(VisitPrepGenerationFailed):
        generate_patient_brief(
            original, protected=protected, record_usage=lambda _: None
        )


@pytest.mark.parametrize("text", ["Injected\x00control", "Injected\tcontrol", 12])
def test_invalid_protected_text_is_content_free_validation_failure(monkeypatch, text):
    _, original = _sources(monkeypatch)
    _deid(monkeypatch)
    protected = protect_patient_analysis(original)
    changed = replace(protected.selection.sources[0], prompt=text)
    with pytest.raises(VisitPrepSynthesisInvalid):
        validate_protected_analysis(
            replace(
                protected,
                selection=replace(
                    protected.selection,
                    sources=(changed, *protected.selection.sources[1:]),
                ),
            ),
            original,
        )


def test_deidentification_worker_failure_precedes_model_and_usage(monkeypatch):
    fixture = _prepared(monkeypatch, "protection-unavailable")
    model = _fake(monkeypatch, _brief(_selected(fixture)).model_dump_json())
    _request(fixture)

    def fail(_body):
        raise RuntimeError("Invented private provider failure")

    _deid(monkeypatch, fail)
    assert run_one_build() == "generation_failed"
    assert model == {} and not _events(fixture) and not _rows(fixture)
    assert _build(fixture).failure_code == "generation_failed"
    # Production deliberately retries a transient provider failure. End this
    # disposable request so later global queue tests cannot pick up its retry.
    from datetime import UTC, datetime, timedelta

    from app.services.visit_prep_synthesis_builds import (
        claim_visit_prep_synthesis,
        fail_visit_prep_synthesis_claim,
    )

    _change_build(fixture, retry_at=datetime.now(UTC) - timedelta(seconds=1))
    claim = claim_visit_prep_synthesis()
    assert claim.visit_prep_id == fixture.prep_id
    assert fail_visit_prep_synthesis_claim(claim, code="scope_unavailable")


@pytest.mark.parametrize("status", [200, 503])
def test_actual_core_deidentification_sdk_uses_stable_bounded_single_request(
    monkeypatch, status
):
    import requests
    from azure.core.credentials import AccessToken
    from azure.core.pipeline.transport import RequestsTransport
    from azure.health.deidentification import DeidentificationClient

    import app.jobs.anonymize as core

    _, original = _sources(monkeypatch)
    calls, clients, options = [], [], []

    class Session:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            body = json.loads(kwargs["data"])
            response = requests.Response()
            response.status_code = status
            response.headers["Content-Type"] = "application/json"
            response._content = json.dumps(
                {
                    "outputText": body["inputText"].replace(
                        "Fictional_Name_Alpha", "Person_Replacement"
                    )
                }
                if status == 200
                else {
                    "error": {
                        "code": "SyntheticFailure",
                        "message": "Invented private provider failure",
                    }
                }
            ).encode()
            response._content_consumed = True
            from types import SimpleNamespace

            response.raw = SimpleNamespace()
            return response

    def client(endpoint, credential, **kwargs):
        options.append(kwargs)
        real = DeidentificationClient(
            endpoint,
            credential,
            transport=RequestsTransport(session=Session(), session_owner=False),
            **kwargs,
        )
        clients.append(real)
        return real

    from types import SimpleNamespace

    credential = SimpleNamespace(
        get_token=lambda *_args, **_kwargs: AccessToken(
            "synthetic-token-not-a-credential", 2_000_000_000
        )
    )
    monkeypatch.setattr(protection, "should_use_test_openai_fallback", lambda: False)
    monkeypatch.setattr(core, "AZURE_DEID_ENDPOINT", "https://synthetic-deid.invalid")
    monkeypatch.setattr(core, "AZURE_DEID_AUTH_MODE", "managed_identity")
    monkeypatch.setattr(core, "ManagedIdentityCredential", lambda **_kwargs: credential)
    monkeypatch.setattr(core, "DeidentificationClient", client)
    with capture_logs() as logs:
        try:
            if status == 200:
                protected = protect_patient_analysis(original)
                assert protected.selection.sources[-1].answer.startswith(
                    "Person_Replacement"
                )
            else:
                with pytest.raises(VisitPrepProtectionFailed):
                    protect_patient_analysis(original)
        finally:
            for real in clients:
                real.close()
    assert len(calls) == 1
    method, url, request = calls[0]
    assert method == "POST" and url.endswith("/deid?api-version=2024-11-15")
    body = json.loads(request["data"])
    assert body["operation"] == "Surrogate"
    assert (
        "VP_BOUNDARY_" in body["inputText"]
        and original.package_id not in body["inputText"]
    )
    assert request["timeout"] == (3.0, 12.0)
    assert options[0]["retry_total"] == 0 and options[0]["logging_enable"] is False
    serialized = json.dumps(logs)
    assert "Fictional_Name_Alpha" not in serialized
    assert "Invented private provider failure" not in serialized


@pytest.mark.parametrize("change", ["remove", "profile", "ref", "words", "extra"])
def test_encrypted_protection_evidence_is_required_and_grounded_on_read(
    monkeypatch, change
):
    _, original = _sources(monkeypatch)
    _deid(monkeypatch)
    protected = protect_patient_analysis(original)
    encrypted = encrypt_synthesis_payload(
        _brief(original), original, protected=protected
    )
    payload = json.loads(decrypt_value(encrypted))
    if change == "remove":
        del payload["protection"]
    elif change == "profile":
        payload["protection"]["profile"] = "raw"
    elif change == "ref":
        payload["protection"]["sources"][0]["source_ref"] = str(
            TypeID("visit_prep_response")
        )
    elif change == "words":
        payload["protection"]["sources"][0]["reported_text"] = (
            "Different invented words"
        )
    else:
        payload["protection"]["sources"][0]["raw_input"] = "forbidden"
    with pytest.raises(VisitPrepSynthesisInvalid):
        decrypt_synthesis_payload(encrypt_value(json.dumps(payload)), original)


def test_unprotected_nonempty_artifact_is_rejected_even_with_grounded_brief(
    monkeypatch,
):
    _, original = _sources(monkeypatch)
    with pytest.raises(VisitPrepSynthesisInvalid):
        encrypt_synthesis_payload(_brief(original), original)


def test_worker_publishes_protected_brief_and_preserves_original_patient_report(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "protected-worker-success")
    _correct(fixture, answer="Fictional_Name_Alpha reports symptoms for 3 days.")
    original = _selected(fixture)
    item = _item(
        original.sources[-1],
        text="Recipient reports symptoms for 3 days.",
        quote="symptoms for 3 days.",
    )
    prepared = _brief(original).model_copy(
        update={"headline": item, "summary": [item], "visit_priorities": []}
    )
    model = _fake(monkeypatch, prepared.model_dump_json())
    calls = _deid(
        monkeypatch,
        lambda body: body.replace("Fictional_Name_Alpha", "Person_Replacement"),
    )
    _request(fixture)
    assert run_one_build() == "ready"
    assert len(calls) == 1 and len(_events(fixture)) == 2
    assert "Fictional_Name_Alpha" not in json.dumps(model["request"])
    view = _view(fixture)
    assert view.prepared == prepared and view.status == "ready"
    assert _selected(fixture) == original
    assert view.package.contribution.topics[-1].answer.startswith(
        "Fictional_Name_Alpha"
    )
    retained = json.loads(decrypt_value(_rows(fixture)[0].payload_ciphertext))
    assert retained["protection"]["profile"] == PROTECTION_PROFILE
    assert (
        "Person_Replacement" in retained["protection"]["sources"][-1]["reported_text"]
    )
    assert run_one_build() == "idle" and len(calls) == 1


def test_expired_lease_after_protection_never_calls_model_or_records_usage(monkeypatch):
    fixture = _prepared(monkeypatch, "protection-expired-lease")
    model = _fake(monkeypatch, _brief(_selected(fixture)).model_dump_json())
    _request(fixture)

    def expire_before_return(body):
        _expire(fixture)
        return body

    _deid(monkeypatch, expire_before_return)
    assert run_one_build() == "discarded"
    assert model == {} and not _events(fixture) and not _rows(fixture)
    from app.services.visit_prep_synthesis_builds import (
        claim_visit_prep_synthesis,
        fail_visit_prep_synthesis_claim,
    )

    claim = claim_visit_prep_synthesis()
    assert claim.visit_prep_id == fixture.prep_id and claim.attempt == 2
    assert fail_visit_prep_synthesis_claim(claim, code="scope_unavailable")


@pytest.mark.parametrize("change", ["correction", "seat", "quarantine"])
def test_changes_during_deidentification_prevent_any_model_call_or_usage(
    monkeypatch, change
):
    fixture = _prepared(monkeypatch, "protection-inflight")
    original = _selected(fixture)
    model = _fake(monkeypatch, _brief(original).model_dump_json())
    _request(fixture)

    def change_before_return(body):
        if change == "correction":
            _correct(fixture, answer="Changed while protection ran.")
        elif change == "seat":
            _withdraw_seat(fixture.owner)
        else:
            report_wrong_recipient(access_token=fixture.access_token)
        return body

    _deid(monkeypatch, change_before_return)
    assert run_one_build() == "discarded"
    assert model == {} and not _events(fixture) and not _rows(fixture)
    if change == "correction":
        assert _build(fixture).epoch == 2 and _build(fixture).state == "pending"
        # End only the newly requeued synthetic request so global polling is isolated.
        from app.services.visit_prep_synthesis_builds import (
            claim_visit_prep_synthesis,
            fail_visit_prep_synthesis_claim,
        )

        claim = claim_visit_prep_synthesis()
        assert claim.visit_prep_id == fixture.prep_id
        assert fail_visit_prep_synthesis_claim(claim, code="scope_unavailable")


def test_protection_holds_no_patient_locks_and_fails_closed_after_concurrent_change(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "protection-free-locks")
    original = _selected(fixture)
    model = _fake(monkeypatch, _brief(original).model_dump_json())
    _request(fixture)
    started, release = Event(), Event()

    def paused(body):
        assert not _events(fixture)
        started.set()
        assert release.wait(timeout=5)
        return body

    _deid(monkeypatch, paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(run_one_build)
        try:
            assert started.wait(timeout=5)
            pool.submit(
                _correct, fixture, answer="Concurrent invented correction."
            ).result(timeout=3)
            release.set()
            assert running.result(timeout=10) == "discarded"
        finally:
            release.set()
    assert model == {} and not _events(fixture) and not _rows(fixture)
    from app.services.visit_prep_synthesis_builds import (
        claim_visit_prep_synthesis,
        fail_visit_prep_synthesis_claim,
    )

    claim = claim_visit_prep_synthesis()
    assert claim.visit_prep_id == fixture.prep_id
    assert fail_visit_prep_synthesis_claim(claim, code="scope_unavailable")


def test_explanations_are_protected_separately_and_originals_remain_available(
    monkeypatch,
):
    from uuid import uuid4

    fixture = _prepared(monkeypatch, "protected-explanation")
    selected = _selected(fixture)
    explain_contribution(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        expected_basis=selected.basis,
        idempotency_key=str(uuid4()),
        question_key="adherence",
        patient_words="Fictional_Name_Alpha sometimes misses a dose.",
    )
    selected = _selected(fixture)
    calls = _deid(
        monkeypatch,
        lambda body: body.replace("Fictional_Name_Alpha", "Person_Replacement"),
    )
    protected = protect_patient_analysis(selected)
    explanation = next(
        source for source in protected.selection.sources if source.explains_source_ref
    )
    assert explanation.answer == "Person_Replacement sometimes misses a dose."
    assert explanation.source_ref in {source.source_ref for source in selected.sources}
    assert len(calls) == 1
    assert "Fictional_Name_Alpha" in next(
        topic.explanation_words
        for topic in _view(fixture).package.contribution.topics
        if topic.explanation_words
    )

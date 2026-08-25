import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
FOUNDRYCTL_PATH = ROOT / "ops/foundry-agents/foundryctl.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "cosmaneura_foundryctl", FOUNDRYCTL_PATH
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
foundryctl = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = foundryctl
MODULE_SPEC.loader.exec_module(foundryctl)


class ScriptedTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.responses.pop(0)


def _bundle(name="deploy-navigator-v1"):
    return foundryctl.render_bundle(foundryctl.load_catalog()[name])


def _remote_version(bundle, version="1"):
    return {
        "object": "agent.version",
        "id": f"{bundle.spec.name}:{version}",
        "name": bundle.spec.name,
        "version": version,
        "status": "active",
        "draft": False,
        "created_at": 1,
        "description": bundle.spec.description,
        "metadata": copy.deepcopy(bundle.create_version_payload["metadata"]),
        "definition": copy.deepcopy(bundle.create_version_payload["definition"]),
    }


def _remote_agent(bundle, version="1"):
    return {
        "object": "agent",
        "id": bundle.spec.name,
        "name": bundle.spec.name,
        "state": "enabled",
        "versions": {"latest": _remote_version(bundle, version)},
        "agent_endpoint": {
            "version_selector": {
                "version_selection_rules": [
                    {
                        "type": "FixedRatio",
                        "agent_version": version,
                        "traffic_percentage": 100,
                    }
                ]
            },
            "protocol_configuration": {"responses": {}},
            "authorization_schemes": [{"type": "Entra"}],
        },
    }


def _walk(value):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def test_all_approved_agents_render_closed_strict_payloads():
    catalog = foundryctl.load_catalog()

    assert set(catalog) == foundryctl.APPROVED_AGENT_NAMES
    for spec in catalog.values():
        bundle = foundryctl.render_bundle(spec)
        payload = bundle.create_version_payload
        assert payload["definition"]["kind"] == "prompt"
        assert payload["definition"]["model"] == "gpt-5.6-sol"
        assert "temperature" not in payload["definition"]
        assert "top_p" not in payload["definition"]
        assert payload["metadata"]["instructions_sha256"] == bundle.instructions_sha256
        assert (
            payload["metadata"]["output_schema_sha256"] == bundle.output_schema_sha256
        )
        assert payload["definition"]["text"]["format"]["strict"] is True
        for node in _walk(bundle.output_schema):
            if isinstance(node, dict):
                assert "$ref" not in node
                assert "allOf" not in node
                assert "if" not in node
                assert not any(key.startswith("x-") for key in node)
        for node in _walk(bundle.foundry_output_schema):
            if isinstance(node, dict):
                assert "const" not in node
                assert (
                    not foundryctl.FOUNDRY_UNSUPPORTED_SCHEMA_CONSTRAINTS.intersection(
                        node
                    )
                )


@pytest.mark.parametrize(
    "stderr",
    [
        'ERROR: Not Found({"error":{"code":"not_found","message":"Agent not found"}})',
        'ERROR: Resource Not Found({"error":{"code":"404"}})',
    ],
)
def test_collision_preflight_treats_live_not_found_shapes_as_absent(stderr):
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=stderr)

    transport = foundryctl.AzureCliRest(runner=runner)
    client = foundryctl.FoundryClient(transport)

    assert client.get_agent("deploy-navigator-v1", missing_ok=True) is None


def test_create_fails_closed_on_name_collision_without_posting():
    bundle = _bundle()
    transport = ScriptedTransport(_remote_agent(bundle))
    client = foundryctl.FoundryClient(transport)

    with pytest.raises(foundryctl.FoundryCtlError, match="name collision"):
        client.create_initial_version(bundle)

    assert [call[0] for call in transport.calls] == ["get"]


def test_create_posts_only_the_rendered_version_after_absence_preflight():
    bundle = _bundle()
    transport = ScriptedTransport(None, _remote_version(bundle))
    client = foundryctl.FoundryClient(transport)

    result = client.create_initial_version(bundle)

    assert result["id"] == "deploy-navigator-v1:1"
    assert [call[0] for call in transport.calls] == ["get", "post"]
    assert transport.calls[1][1].endswith(
        "/agents/deploy-navigator-v1/versions?api-version=v1"
    )
    assert transport.calls[1][2]["body"] == bundle.create_version_payload


def test_route_verifies_exact_version_before_single_patch():
    bundle = _bundle()
    transport = ScriptedTransport(_remote_version(bundle), _remote_agent(bundle))
    client = foundryctl.FoundryClient(transport)

    result = client.route_version(bundle, "1")

    assert result["agent_endpoint"]["version_selector"]["version_selection_rules"] == [
        {"type": "FixedRatio", "agent_version": "1", "traffic_percentage": 100}
    ]
    assert [call[0] for call in transport.calls] == ["get", "patch"]
    patch_body = transport.calls[1][2]["body"]
    assert patch_body["agent_endpoint"]["authorization_schemes"] == [{"type": "Entra"}]


def test_transport_rejects_unapproved_mutations_and_nonstored_invocations():
    transport = foundryctl.AzureCliRest(
        runner=lambda *args, **kwargs: pytest.fail("runner must not be called")
    )

    with pytest.raises(foundryctl.FoundryCtlError, match="unsupported"):
        transport.request(
            "delete",
            f"{foundryctl.PROJECT_ENDPOINT}/agents/deploy-navigator-v1?api-version=v1",
        )
    with pytest.raises(foundryctl.FoundryCtlError, match="store=false"):
        transport.request(
            "post",
            f"{foundryctl.PROJECT_ENDPOINT}/openai/v1/responses",
            body={"store": True},
        )


def test_transport_rejects_unapproved_agent_names_before_running_azure_cli():
    transport = foundryctl.AzureCliRest(
        runner=lambda *args, **kwargs: pytest.fail("runner must not be called")
    )

    with pytest.raises(foundryctl.FoundryCtlError, match="approved catalog"):
        transport.request(
            "get",
            f"{foundryctl.PROJECT_ENDPOINT}/agents/codebase-q-a-assistant?api-version=v1",
        )


def test_transport_requires_exact_create_route_and_invoke_bodies():
    transport = foundryctl.AzureCliRest(
        runner=lambda *args, **kwargs: pytest.fail("runner must not be called")
    )
    bundle = _bundle()
    create_body = copy.deepcopy(bundle.create_version_payload)
    create_body["description"] = "mutated"
    with pytest.raises(foundryctl.FoundryCtlError, match="rendered local bundle"):
        transport.request(
            "post",
            f"{foundryctl.PROJECT_ENDPOINT}/agents/{bundle.spec.name}/versions?api-version=v1",
            body=create_body,
        )

    route_body = foundryctl._route_patch_body("2")
    route_body["state"] = "enabled"
    with pytest.raises(foundryctl.FoundryCtlError, match="exactly match"):
        transport.request(
            "patch",
            f"{foundryctl.PROJECT_ENDPOINT}/agents/{bundle.spec.name}?api-version=v1",
            body=route_body,
            content_type="application/merge-patch+json",
        )

    invoke_body = {
        "agent_reference": {
            "type": "agent_reference",
            "name": bundle.spec.name,
            "version": "2",
        },
        "input": '{"synthetic":true}',
        "max_output_tokens": bundle.spec.max_output_tokens,
        "store": False,
        "model": bundle.spec.model,
    }
    with pytest.raises(foundryctl.FoundryCtlError, match="exact permitted fields"):
        transport.request(
            "post",
            f"{foundryctl.PROJECT_ENDPOINT}/openai/v1/responses",
            body=invoke_body,
        )

    for invalid_input in ('{"value":NaN}', '{"value":1,"value":2}'):
        invalid_invoke = {
            "agent_reference": {
                "type": "agent_reference",
                "name": bundle.spec.name,
                "version": "2",
            },
            "input": invalid_input,
            "max_output_tokens": bundle.spec.max_output_tokens,
            "store": False,
        }
        with pytest.raises(foundryctl.FoundryCtlError, match="valid JSON"):
            transport.request(
                "post",
                f"{foundryctl.PROJECT_ENDPOINT}/openai/v1/responses",
                body=invalid_invoke,
            )


def test_parse_output_text_handles_raw_responses_shape():
    value = {"draft_version": "cosmaneura.agent-assessment-draft.v1"}
    response = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(value)}],
            }
        ]
    }

    assert foundryctl.parse_output_text(response) == value


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"synthetic":true,"synthetic":false}', "duplicate JSON key"),
        ('{"score":NaN}', "non-JSON numeric constant"),
    ],
)
def test_load_evidence_rejects_non_strict_json(tmp_path, raw, expected):
    path = tmp_path / "evidence.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(foundryctl.FoundryCtlError, match=expected):
        foundryctl._load_evidence(path)


def test_load_evidence_rejects_symbolic_links(tmp_path):
    target = tmp_path / "target.json"
    target.write_text('{"synthetic":true}', encoding="utf-8")
    link = tmp_path / "evidence.json"
    link.symlink_to(target)

    with pytest.raises(foundryctl.FoundryCtlError, match="symbolic link"):
        foundryctl._load_evidence(link)


def test_full_local_schema_enforces_constraints_removed_from_foundry_projection():
    bundle = _bundle()
    draft = {
        "draft_version": "cosmaneura.agent-assessment-draft.v1",
        "kind": "deployment_assessment",
        "coverage": {
            "included": ["duplicate", "duplicate"],
            "omitted": [],
            "evidence_fresh_until": "not-a-date-time",
        },
        "decision": {
            "status": "READY",
            "risk_tier": "T0",
            "summary": "Synthetic evidence is internally consistent.",
        },
        "payload": {
            "target_environment": "staging",
            "required_checks": [],
            "migration_review_required": False,
            "rollback_reference": None,
        },
        "actions": [],
        "constraints": {
            "must_not": ["Modify production."],
            "approvals_required": [],
        },
    }

    errors = foundryctl.validate_json_instance(draft, bundle.output_schema)

    assert any("unique items" in error for error in errors)
    assert any("ISO-8601 date-time" in error for error in errors)
    assert not foundryctl.validate_json_instance(draft, bundle.foundry_output_schema)


def test_invoke_atomically_writes_validated_draft_output(tmp_path, capsys):
    draft = {
        "draft_version": "cosmaneura.agent-assessment-draft.v1",
        "kind": "deployment_assessment",
        "coverage": {
            "included": ["synthetic diff"],
            "omitted": ["production telemetry"],
            "evidence_fresh_until": "2026-08-25T20:00:00Z",
        },
        "decision": {
            "status": "READY",
            "risk_tier": "T0",
            "summary": "Synthetic evidence is internally consistent.",
        },
        "payload": {
            "target_environment": "staging",
            "required_checks": [],
            "migration_review_required": False,
            "rollback_reference": None,
        },
        "actions": [],
        "constraints": {
            "must_not": ["Modify production."],
            "approvals_required": [],
        },
    }
    response = {
        "id": "resp_test",
        "object": "response",
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "agent_reference": {
            "type": "agent_reference",
            "name": "deploy-navigator-v1",
            "version": "1",
        },
        "output_text": json.dumps(draft),
    }
    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(response), stderr=""
        )

    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text('{"synthetic":true}', encoding="utf-8")
    draft_path = tmp_path / "draft.json"

    result = foundryctl.run(
        [
            "invoke",
            "deploy-navigator-v1",
            "--input",
            str(evidence_path),
            "--version",
            "1",
            "--confirm-sanitized",
            "--draft-output",
            str(draft_path),
        ],
        runner=runner,
    )

    assert result == 0
    assert json.loads(draft_path.read_text(encoding="utf-8")) == draft
    assert draft_path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.tmp-*"))
    request_body = json.loads(commands[0][commands[0].index("--body") + 1])
    assert request_body["store"] is False
    assert request_body["agent_reference"]["version"] == "1"
    assert json.loads(capsys.readouterr().out) == draft

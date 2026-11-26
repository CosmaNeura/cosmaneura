"""Render plan actions using static addresses from the reviewed Terraform source."""

import collections
import json
import re
import sys

from contract import REPO_ROOT, Stop, require

IDENT = r"[A-Za-z_][A-Za-z0-9_-]*"
# Instance indexes can hold arbitrary user strings. Never render them.
INSTANCE_KEY = r'\[(?:[0-9]+|"(?:[^"\\]|\\.)*")\]'
INDEX = f"(?:{INSTANCE_KEY})?"
ACTIONS = {
    ("no-op",): "unchanged",
    ("read",): "read",
    ("create",): "create",
    ("update",): "update",
    ("delete",): "delete",
    ("delete", "create"): "replace (delete first)",
    ("create", "delete"): "replace (create first)",
    ("forget",): "remove from state",
}


def source_catalog(root):
    """Index literal HCL declarations in the root and declared local modules."""
    declarations = set()
    for source in sorted(root.glob("*.tf")):
        text = source.read_text()
        for match in re.finditer(
            rf'^\s*(resource|data) "({IDENT})" "({IDENT})"\s*{{', text, re.M
        ):
            mode, kind, name = match.groups()
            declarations.add(f"{'data.' if mode == 'data' else ''}{kind}.{name}")
        for match in re.finditer(rf'^\s*output "({IDENT})"\s*{{', text, re.M):
            declarations.add(f"output.{match[1]}")
        for match in re.finditer(
            rf'^module "({IDENT})"\s*{{[^{{}}]*?\bsource\s*=\s*"\./([a-zA-Z0-9_/-]+)"',
            text,
            re.M,
        ):
            declarations.update(
                f"module.{match[1]}.{name}" for name in source_catalog(root / match[2])
            )
    return declarations


def literal_address(address, catalog):
    require(
        re.fullmatch(
            rf"(?:module\.{IDENT}{INDEX}\.)*(?:data\.)?{IDENT}\.{IDENT}{INDEX}", address
        ),
        "plan_address",
    )
    literal = re.sub(INSTANCE_KEY, "", address)
    require(literal in catalog, "plan_unreviewed_address")
    return literal


def render(plan, catalog):
    require(
        plan["format_version"] == "1.2"
        and plan.get("errored") is not True
        and plan.get("complete") is not False
        and not plan.get("deferred_changes")
        and not plan.get("action_invocations"),
        "plan_format_or_incomplete",
    )
    rows = collections.Counter()
    for key, category in (("resource_changes", "Plan"), ("resource_drift", "Drift")):
        for resource in plan.get(key, []):
            address = literal_address(resource["address"], catalog)
            change = resource["change"]
            action = ACTIONS[tuple(change["actions"])]
            require(
                "importing" not in change
                and "previous_address" not in resource
                and "deposed" not in resource,
                "plan_unsupported_operation",
            )
            if action != "unchanged":
                rows[category, address, action] += 1
    for name, change in plan.get("output_changes", {}).items():
        address = f"output.{name}"
        require(address in catalog, "plan_unreviewed_output")
        action = ACTIONS[tuple(change["actions"])]
        if action != "unchanged":
            rows["Output", address, action] += 1
    require(len(rows) <= 250, "plan_summary_size")
    result = [
        "## Protected Terraform Plan",
        "",
        "Review actions by source address. Instance keys, attribute names and values are omitted.",
        "Stop if this detail is insufficient to approve the change.",
        "",
    ]
    if rows:
        result.extend(
            [
                "| Section | Source address | Action | Count |",
                "| --- | --- | --- | ---: |",
            ]
        )
        result.extend(
            f"| {category} | `{address}` | {action} | {count} |"
            for (category, address, action), count in sorted(rows.items())
        )
    else:
        result.append("No resource or output changes or drift.")
    return "\n".join(result) + "\n"


def main():
    try:
        summary = render(json.load(sys.stdin), source_catalog(REPO_ROOT / "infra"))
        # Build all output in memory so refusal cannot emit a partial review.
        sys.stdout.write(summary)
    except (Stop, OSError, TypeError, ValueError, KeyError, AttributeError):
        print("OpenTofu plan could not be rendered safely.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

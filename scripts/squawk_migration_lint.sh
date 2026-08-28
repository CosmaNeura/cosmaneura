#!/usr/bin/env zsh

set -euo pipefail

if [[ "$#" -ne 1 || -z "$1" ]]; then
	echo "usage: $0 <alembic-base-revision>" >&2
	exit 2
fi

migration_sql=""
squawk_report=""
cleanup() {
	[[ -z "$migration_sql" ]] || rm -f -- "$migration_sql"
	[[ -z "$squawk_report" ]] || rm -f -- "$squawk_report"
}
trap cleanup EXIT HUP INT TERM
migration_sql=$(mktemp)
squawk_report=$(mktemp)

# Materialize Alembic's output first so its failure cannot be hidden by
# Squawk's intentional nonzero status when it reports lint findings.
LOG_LEVEL=error uv run alembic upgrade "${1}:head" --sql > "$migration_sql"

squawk_rc=0
LOG_LEVEL=error uv run squawk --reporter=json --exclude=prefer-text-field \
	< "$migration_sql" > "$squawk_report" || squawk_rc=$?

# Squawk uses status 1 for a valid non-empty findings report. Any other
# nonzero status is an execution failure, not a lint policy decision.
if (( squawk_rc > 1 )); then
	echo "Squawk execution failed with status $squawk_rc" >&2
	exit "$squawk_rc"
fi
if ! jq -e '
	type == "array" and all(.[];
		type == "object" and
		(.level == "Warning" or .level == "Error") and
		(.file | type == "string" and length > 0) and
		(.line | type == "number" and . >= 0 and floor == .) and
		(.column | type == "number" and . >= 0 and floor == .) and
		(.rule_name | type == "string" and length > 0) and
		(.messages |
			type == "array" and length > 0 and
			(.[0] | type == "object" and (.Note | type == "string" and length > 0)) and
			all(.[];
				type == "object" and
				(keys | length == 1 and all(.[]; . == "Note" or . == "Help")) and
				((.Note? // .Help?) | type == "string" and length > 0)
			)
		)
	)' "$squawk_report" > /dev/null; then
	echo "Squawk did not produce a valid supported JSON findings array" >&2
	exit 1
fi
finding_count=$(jq 'length' "$squawk_report")
if (( squawk_rc == 1 && finding_count == 0 )); then
	echo "Squawk failed without reporting a lint finding" >&2
	exit 1
fi

if [[ -n "${CI:-}" ]]; then
	jq -r '.[] | "::\(if .level == "Error" then "error" else "warning" end) file=\(.file),line=\(.line),col=\(.column),title=\(.rule_name)::\(.messages[0].Note)"' "$squawk_report"
else
	jq -r '.[] | "\(.file):\(.line):\(.column): \(.level) [\(.rule_name)] \(.messages[0].Note)"' "$squawk_report"
fi

# Warnings are advisory by existing policy; Error findings fail the recipe.
error_count=$(jq '[.[] | select(.level == "Error")] | length' "$squawk_report")
(( error_count == 0 ))

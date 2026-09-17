#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

payload="$(python3 -c 'import base64,sys,zlib;print(base64.b64encode(zlib.compress(open(sys.argv[1],"rb").read(),9)).decode())' "$ROOT/infra-staging/provider_v7_acceptance.py")"
command="PYTHONPATH=/app /opt/venv/bin/python -c \"import base64,zlib;exec(zlib.decompress(base64.b64decode('$payload')))\" --status-only"
[[ ${#command} -lt 2000 ]] || { echo "V7_STATUS FAIL"; exit 1; }
/usr/bin/expect "$ROOT/infra-staging/containerapp-exec.expect" \
	cosmaneura-staging cosmaneura-staging-app "$command" "V7_STATUS PASS" \
	>/dev/null 2>&1 || {
	echo "V7_STATUS FAIL"
	exit 1
}
echo "V7_STATUS PASS"

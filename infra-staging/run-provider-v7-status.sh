#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

read -r -d '' PROBE <<'PY' || true
import hashlib,os
from app.services.authority_reconciliation import incomplete_ingestion_status
try:
    if os.environ.get("PYTHON_ENV")!="staging" or os.environ.get("AUTHORITY_KERNEL_ENABLED","").lower()!="true" or os.environ.get("AGENT_API_ENABLED","").lower()!="true": raise Exception
    r=incomplete_ingestion_status(older_than_minutes=15,limit=1000); rows=r.interactions
    actual=(r.matching_interaction_count,r.pending_audit_count,r.failed_audit_count,r.pending_usage_count,r.failed_usage_count,len(rows),rows[0].audit_status,rows[0].usage_status,hashlib.sha256(rows[0].interaction_ref.encode()).hexdigest())
    if actual!=(1,0,1,0,1,1,"failed","failed","94527b2939fa327ee1a355c94749ef507e47db88445f3f8cabefc2befb44a340"): raise Exception
except Exception:
    print("V7_STATUS FAIL");raise SystemExit(1)
print("V7_STATUS PASS")
PY

payload="$(printf %s "$PROBE" | python3 -c 'import base64,sys,zlib;print(base64.b64encode(zlib.compress(sys.stdin.buffer.read(),9)).decode())')"
command="PYTHONPATH=/app /opt/venv/bin/python -c \"import base64,zlib;exec(zlib.decompress(base64.b64decode('$payload')))\""
[[ ${#command} -lt 1000 ]] || { echo "V7_STATUS FAIL"; exit 1; }
/usr/bin/expect "$ROOT/infra-staging/containerapp-exec.expect" \
	cosmaneura-staging cosmaneura-staging-app "$command" "V7_STATUS PASS" \
	>/dev/null 2>&1 || {
	echo "V7_STATUS FAIL"
	exit 1
}
echo "V7_STATUS PASS"

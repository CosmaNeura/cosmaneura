from __future__ import annotations

import ipaddress
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


class ProbeFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _is_private_address(address: str) -> bool:
    parsed = ipaddress.ip_address(address)
    return any(parsed in network for network in _PRIVATE_NETWORKS)


def resolve_private_addresses(hostname: str) -> tuple[str, ...]:
    try:
        records = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ProbeFailure("dns_resolution_failed") from exc

    addresses = tuple(sorted({str(record[4][0]) for record in records}))
    if not addresses:
        raise ProbeFailure("dns_no_addresses")
    if not all(_is_private_address(address) for address in addresses):
        raise ProbeFailure("dns_not_private")
    return addresses


def _validated_endpoints(health_url: str, workflow_url: str) -> tuple[str, str]:
    health = urllib.parse.urlsplit(health_url)
    workflow = urllib.parse.urlsplit(workflow_url)
    if health.scheme != "https" or workflow.scheme != "https":
        raise ProbeFailure("https_required")
    if not health.hostname or not workflow.hostname:
        raise ProbeFailure("hostname_required")
    if health.hostname != workflow.hostname:
        raise ProbeFailure("endpoint_host_mismatch")
    return health.hostname, workflow.hostname


def _request_status(url: str, *, method: str, timeout_seconds: int) -> int:
    request = urllib.request.Request(
        url,
        data=b"{}" if method == "POST" else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        return status
    except (OSError, urllib.error.URLError) as exc:
        raise ProbeFailure("https_request_failed") from exc


def run_probe(
    health_url: str, workflow_url: str, *, timeout_seconds: int = 10
) -> dict[str, object]:
    hostname, _ = _validated_endpoints(health_url, workflow_url)
    resolve_private_addresses(hostname)

    health_status = _request_status(
        health_url, method="GET", timeout_seconds=timeout_seconds
    )
    if health_status != 200:
        raise ProbeFailure("health_status_unexpected")

    workflow_status = _request_status(
        workflow_url,
        method="POST",
        timeout_seconds=timeout_seconds,
    )
    if workflow_status != 401:
        raise ProbeFailure("unauthenticated_status_unexpected")

    return {
        "dns": "private",
        "health_status": health_status,
        "result": "passed",
        "unauthenticated_workflow_status": workflow_status,
    }


def main() -> int:
    health_url = os.environ.get("SCRIBE_HEALTH_URL", "")
    workflow_url = os.environ.get("SCRIBE_UNAUTHENTICATED_WORKFLOW_URL", "")
    try:
        timeout_seconds = int(os.environ.get("PROBE_TIMEOUT_SECONDS", "10"))
        if not 1 <= timeout_seconds <= 30:
            raise ProbeFailure("timeout_out_of_range")
        result = run_probe(health_url, workflow_url, timeout_seconds=timeout_seconds)
    except (ProbeFailure, ValueError) as exc:
        code = exc.code if isinstance(exc, ProbeFailure) else "timeout_invalid"
        print(json.dumps({"code": code, "result": "failed"}, sort_keys=True))
        return 1
    except Exception:
        print(
            json.dumps({"code": "unexpected_error", "result": "failed"}, sort_keys=True)
        )
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

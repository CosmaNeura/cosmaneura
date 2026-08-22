import os
import sys


# fmt: off
def main():
    try:
        e = os.environ
        if not (len(sys.argv) == 4 and e.get("MOUNT_PROFILE") == "public" and e.get("PLATFORM_AGENT_API_ENABLED") in {None, "false"}):
            raise Exception
        import azure.identity as a
        import httpx as h
        o, s, c = sys.argv[1:]
        t = a.ManagedIdentityCredential(client_id=c).get_token(s).token
        r = h.post(o + "/service/v1/agent/mount-context/bootstrap", headers={"Authorization": "Bearer " + t, "X-Cosmaneura-Mount-Context": "mctx_00000000000"}, timeout=20)
        b = r.json()
        if (r.status_code, b.get("status"), b.get("code")) != (403, 403, "WORKLOAD_NOT_ALLOWED"):
            raise Exception
    except Exception:
        print("V7_PUBLIC_DENIAL FAIL")
        return 1
    print("V7_PUBLIC_DENIAL PASS")
    return 0
# fmt: on

if __name__ == "__main__":
    raise SystemExit(main())

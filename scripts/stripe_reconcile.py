# /// script
# requires-python = ">=3.12"
# ///
"""One-time Stripe reconciliation for marketing-site subscribers.

Before 2026-08-20 the marketing site sold $89/mo subscriptions whose only
durable record was a Cloudflare Workers log line (AUDIT.md §3.6 #1). This
lists every subscription and its customer straight from Stripe so each one
can be matched to a Cosmas account, comped via billing_exempt, or refunded.

Usage (needs the live secret key; read-only requests only):

    STRIPE_SECRET_KEY=sk_live_... uv run scripts/stripe_reconcile.py
    STRIPE_SECRET_KEY=sk_live_... uv run scripts/stripe_reconcile.py --csv > subscribers.csv

Stdlib only — no dependencies to install.
"""

import csv
import io
import json
import os
import sys
import urllib.parse
import urllib.request

API = "https://api.stripe.com/v1"


def stripe_get(key: str, path: str, params: dict[str, str]) -> dict:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{API}{path}?{query}", headers={"Authorization": f"Bearer {key}"}
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def all_subscriptions(key: str) -> list[dict]:
    subscriptions: list[dict] = []
    params = {"status": "all", "limit": "100", "expand[]": "data.customer"}
    while True:
        page = stripe_get(key, "/subscriptions", params)
        subscriptions.extend(page["data"])
        if not page.get("has_more"):
            return subscriptions
        params["starting_after"] = page["data"][-1]["id"]


def main() -> int:
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        print("Set STRIPE_SECRET_KEY (sk_live_... for production).", file=sys.stderr)
        return 1

    rows = []
    for subscription in all_subscriptions(key):
        customer = subscription.get("customer") or {}
        if isinstance(customer, str):
            customer = {"id": customer}
        rows.append(
            {
                "subscription_id": subscription["id"],
                "status": subscription["status"],
                "customer_id": customer.get("id", ""),
                "email": customer.get("email") or "",
                "name": customer.get("name") or "",
                "created_unix": subscription.get("created", ""),
                "cancel_at_period_end": subscription.get("cancel_at_period_end", False),
            }
        )

    if "--csv" in sys.argv:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
        print(buffer.getvalue(), end="")
    else:
        if not rows:
            print("No subscriptions found.")
        for row in rows:
            print(
                f"{row['status']:>10}  {row['subscription_id']}  "
                f"{row['email'] or '(no email)'}  {row['name']}"
            )
        print(
            f"\n{len(rows)} subscription(s). For each: match to a Cosmas "
            "account and set billing_exempt on its practice, or refund via "
            "the Stripe dashboard.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Dry-run-first manual QA bootstrap. No app import before target validation."""

import argparse
import hashlib
import hmac
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

EMAILS = ("biller@cosmaneura.com", "doctor@cosmaneura.com")


def validate_target(env: dict[str, str], host: str, database: str) -> None:
    """Operator pins must come from independently verified staging inventory."""
    if env.get("PYTHON_ENV") != "staging":
        raise ValueError("PYTHON_ENV must be staging")
    if env.get("QA_SYNTHETIC_ONLY") != "yes":
        raise ValueError(
            "Confirm the target is synthetic-only with QA_SYNTHETIC_ONLY=yes"
        )
    if env.get("AUTO_MIGRATE_ON_STARTUP", "").lower() != "false":
        raise ValueError("AUTO_MIGRATE_ON_STARTUP must be false")
    url = urlsplit(env.get("DATABASE_URL", ""))
    if (
        not host
        or not database
        or url.scheme not in {"postgresql", "postgresql+psycopg"}
        or url.hostname != host
        or url.path != f"/{database}"
        or url.query
        or url.fragment
    ):
        raise ValueError(
            "Database does not match the independently verified staging target"
        )
    key = env.get("CLERK_PRIVATE_KEY", "")
    pin = env.get("QA_CLERK_KEY_SHA256", "")
    if (
        not key.startswith("sk_test_")
        or len(pin) != 64
        or not hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(), pin)
    ):
        raise ValueError("Clerk test key does not match the staging key fingerprint")
    if env.get("STRIPE_LIVE_MODE", "").lower() != "false":
        raise ValueError("STRIPE_LIVE_MODE must explicitly be false")
    if not env.get("STRIPE_SECRET_KEY", "").startswith(("sk_test_", "rk_test_")):
        raise ValueError("A Stripe test-mode key is required; live keys are refused")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("accounts", "fixtures"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--database-host", default="")
    parser.add_argument("--database-name", default="")
    parser.add_argument("--practice-id", default="")
    args = parser.parse_args(argv)
    if not args.apply:
        print("DRY RUN: no connections, imports, or writes")
        print("accounts: create seed-owned identities; preserve existing passwords")
        print("biller@cosmaneura.com: owner (billing and clinical)")
        print("doctor@cosmaneura.com: workforce (clinical, no owner billing controls)")
        print(
            "fixtures: synthetic patients and visits after real onboarding and test Checkout"
        )
        return 0
    try:
        validate_target(dict(os.environ), args.database_host, args.database_name)
        if args.phase == "fixtures" and not args.practice_id.startswith("practice_"):
            raise ValueError("fixtures requires the exact QA --practice-id")
        passwords = {
            email: os.environ.get(variable, "")
            for email, variable in zip(
                EMAILS, ("QA_BILLER_PASSWORD", "QA_DOCTOR_PASSWORD"), strict=True
            )
        }
        if args.phase == "accounts" and any(len(p) < 16 for p in passwords.values()):
            raise ValueError("Set both QA password variables (16 characters minimum)")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        # Import-time application setup is preserved, but migration is disabled
        # and every explicit target/secret guard has already passed.
        if args.phase == "accounts":
            from app.configuration.clerk import provision_manual_qa_users

            provision_manual_qa_users(passwords)
            print(
                "QA identities ready; complete the one-time onboarding in the runbook"
            )
        else:
            from app.services.manual_qa_seed import seed_manual_qa_fixtures

            seed_manual_qa_fixtures(args.practice_id)
            print("Synthetic QA patients and visits ready; existing data unchanged")
        return 0
    except Exception:
        # Vendor/driver exceptions can contain credentials or response bodies.
        # Do not print them, even in verbose mode. Detailed troubleshooting is
        # via the operator runbook, not a secret-bearing traceback.
        print(
            "Seed refused or failed. Check target, account ownership, onboarding, and runtime configuration.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

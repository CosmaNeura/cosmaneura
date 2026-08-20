"""Practice work-email domain matching for workforce join."""

MCD_EMAIL_DOMAIN = "mycatholicdoctor.com"


def extract_email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[-1].strip().lower()
    return domain or None


def email_matches_domain(email: str | None, required_domain: str | None) -> bool:
    got = extract_email_domain(email)
    required = (required_domain or "").strip().lower()
    if not got or not required:
        return False
    return got == required or got.endswith(f".{required}")


def is_mcd_email(email: str | None) -> bool:
    return email_matches_domain(email, MCD_EMAIL_DOMAIN)

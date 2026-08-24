"""Practice work-email domain matching for workforce join."""

MCD_EMAIL_DOMAIN = "mycatholicdoctor.com"

# Consumer mailbox providers are not an organizational boundary. Keep this
# list deliberately explicit: a custom domain hosted by any of these providers
# is still a practice-owned domain and is therefore not included here.
PUBLIC_EMAIL_DOMAINS = frozenset(
    {
        "aol.com",
        "fastmail.com",
        "gmail.com",
        "gmx.com",
        "gmx.us",
        "googlemail.com",
        "hey.com",
        "hotmail.com",
        "icloud.com",
        "live.com",
        "mac.com",
        "mail.com",
        "me.com",
        "msn.com",
        "outlook.com",
        "pm.me",
        "proton.me",
        "protonmail.com",
        "yahoo.com",
        "ymail.com",
        "zoho.com",
    }
)


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


def is_public_email_domain(domain: str | None) -> bool:
    """Return whether *domain* is a shared consumer mailbox domain."""

    normalized = (domain or "").strip().lower().lstrip("@")
    return normalized in PUBLIC_EMAIL_DOMAINS

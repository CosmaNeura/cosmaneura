"""Fail-closed Clerk identity facts used by privileged enrollment flows."""

from app.configuration.clerk import clerk


def verified_primary_email(clerk_id: str) -> str | None:
    """Return Clerk's verified primary email, never the first arbitrary email."""

    try:
        user = clerk.users.get(user_id=clerk_id)
    except Exception:
        return None
    if (
        user is None
        or getattr(user, "id", None) != clerk_id
        or getattr(user, "banned", None) is not False
        or getattr(user, "locked", None) is not False
        or not user.email_addresses
    ):
        return None
    primary_id = user.primary_email_address_id
    if not isinstance(primary_id, str) or not primary_id:
        return None
    primary_matches = [
        address for address in user.email_addresses if address.id == primary_id
    ]
    if len(primary_matches) != 1:
        return None
    primary = primary_matches[0]
    verification = getattr(primary, "verification", None)
    verification_status = getattr(verification, "status", None)
    # Clerk permits a null verification object/status.  This enrollment path
    # is authorization-sensitive, so missing evidence must fail closed rather
    # than being treated as equivalent to a verified address.
    if verification_status != "verified":
        return None
    email = getattr(primary, "email_address", None)
    if not isinstance(email, str):
        return None
    normalized = email.strip().lower()
    if (
        not normalized
        or normalized.count("@") != 1
        or not normalized.rsplit("@", 1)[0]
        or "." not in normalized.rsplit("@", 1)[1]
    ):
        return None
    return normalized

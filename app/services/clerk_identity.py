"""Fail-closed Clerk identity facts used by privileged enrollment flows."""

from app.configuration.clerk import clerk


def verified_primary_email(clerk_id: str) -> str | None:
    """Return Clerk's verified primary email, never the first arbitrary email."""

    try:
        user = clerk.users.get(user_id=clerk_id)
    except Exception:
        return None
    if user is None or not user.email_addresses:
        return None
    primary_id = user.primary_email_address_id
    primary = next(
        (address for address in user.email_addresses if address.id == primary_id),
        None,
    )
    if primary is None:
        return None
    verification = getattr(primary, "verification", None)
    verification_status = getattr(verification, "status", None)
    # Clerk permits a null verification object/status.  This enrollment path
    # is authorization-sensitive, so missing evidence must fail closed rather
    # than being treated as equivalent to a verified address.
    if verification_status != "verified":
        return None
    return primary.email_address.strip().lower()

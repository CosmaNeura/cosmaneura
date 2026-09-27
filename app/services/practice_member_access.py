"""Owner revocation and restoration of practice members' access.

A revocation is reversible and takes effect at the next midnight on US Eastern
clock time; until then the member keeps access and holds their seat.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ACCESS_CUTOFF_ZONE = ZoneInfo("America/New_York")


def access_cutoff_after(now: datetime) -> datetime:
    """The next US Eastern midnight strictly after ``now``, in UTC."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("access cut-off requires a timezone-aware datetime")
    eastern_date = now.astimezone(ACCESS_CUTOFF_ZONE).date()
    return datetime.combine(
        eastern_date + timedelta(days=1), time(0), ACCESS_CUTOFF_ZONE
    ).astimezone(timezone.utc)

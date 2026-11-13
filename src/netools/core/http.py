from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def retry_after(value: str | None) -> float:
    """Parse a Retry-After header (seconds or HTTP date) into a bounded cooldown."""
    try:
        seconds = float(value) if value is not None else 30.0
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            seconds = 30.0
    # Persist a long Retry-After; the request can fail over to another account.
    return max(1.0, min(seconds, 86400.0))

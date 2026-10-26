"""compute_deadline (Tabel 3.6, UT-BE-08)."""
from __future__ import annotations

from datetime import datetime, timezone

RECEIVED_AT_HEADER = "X-Request-Received-At"


def compute_deadline(received_at: datetime, now: datetime, budget_ms: int = 8000,
                     response_margin_ms: int = 500) -> int:
    """Milliseconds the Route Model may use: budget - time already spent - response margin.

    UT-BE-08: 2 000 ms spent with the defaults gives 5 500; 1 000 ms spent with
    budget 6 000 and margin 300 gives 4 700. May be zero or negative.
    """
    spent_ms = (now - received_at).total_seconds() * 1000
    return int(budget_ms - spent_ms - response_margin_ms)


def parse_received_at(value: str | None) -> datetime | None:
    """X-Request-Received-At from the API Gateway (ISO 8601); None when absent or invalid."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

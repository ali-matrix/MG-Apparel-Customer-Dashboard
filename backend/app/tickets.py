"""Support tickets. A single INSERT, so a ticket is written completely or not at all."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from .db import get_conn

DUPLICATE_WINDOW = timedelta(minutes=5)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_ticket(customer_id: str, po_no: Optional[str], message: str) -> str:
    """Create a ticket and return its id. An identical request sent again within
    a few minutes (double click, retry) returns the existing ticket instead."""
    con = get_conn()
    now = _utcnow()
    since = now - DUPLICATE_WINDOW

    if po_no is None:
        existing = con.execute(
            "SELECT ticket_id FROM tickets WHERE customer_id = ? AND po_no IS NULL "
            "AND message = ? AND created_at > ? LIMIT 1",
            [customer_id, message, since],
        ).fetchone()
    else:
        existing = con.execute(
            "SELECT ticket_id FROM tickets WHERE customer_id = ? AND po_no = ? "
            "AND message = ? AND created_at > ? LIMIT 1",
            [customer_id, po_no, message, since],
        ).fetchone()
    if existing:
        return existing[0]

    ticket_id = "TCK-" + uuid.uuid4().hex[:8].upper()
    con.execute(
        "INSERT INTO tickets (ticket_id, customer_id, po_no, message, status, created_at) "
        "VALUES (?, ?, ?, ?, 'open', ?)",
        [ticket_id, customer_id, po_no, message, now],
    )
    return ticket_id
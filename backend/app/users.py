"""Customer logins. Kept separate from sync so a sync never touches passwords."""
import logging
from datetime import datetime, timezone
from typing import Optional

from .config import SEED_PASSWORD
from .db import get_conn
from .security import DUMMY_HASH, hash_password, verify_password

log = logging.getLogger("users")


def seed_users() -> int:
    """Create a login for every active customer that has none. Safe to re-run."""
    con = get_conn()
    missing = con.execute(
        "SELECT c.customer_id, c.portal_email FROM customers c "
        "LEFT JOIN users u ON u.customer_id = c.customer_id "
        "WHERE c.is_active AND u.customer_id IS NULL"
    ).fetchall()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for customer_id, email in missing:
        con.execute(
            "INSERT INTO users (customer_id, email, password_hash, created_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (customer_id) DO NOTHING",
            [customer_id, email, hash_password(SEED_PASSWORD), now],
        )
    if missing:
        log.info("Created %d customer logins", len(missing))
    return len(missing)


def authenticate(email: str, password: str) -> Optional[dict]:
    row = get_conn().execute(
        "SELECT c.customer_id, c.company_name, u.password_hash "
        "FROM customers c JOIN users u ON u.customer_id = c.customer_id "
        "WHERE lower(c.portal_email) = ? AND c.is_active",
        [email.strip().lower()],
    ).fetchone()
    if row is None:
        verify_password(password, DUMMY_HASH)  # same timing as a real check
        return None
    if not verify_password(password, row[2]):
        return None
    return {"customer_id": row[0], "company_name": row[1]}
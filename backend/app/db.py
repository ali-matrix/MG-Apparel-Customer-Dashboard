"""DuckDB connection + schema."""
import logging
import threading

import duckdb

from .config import DB_PATH

log = logging.getLogger("db")

_lock = threading.Lock()
_root_con = None

# NOTE: no FOREIGN KEY / secondary indexes on purpose. DuckDB has known
# limitations when upserting rows that are referenced by FKs, and with a few
# thousand rows a columnar scan is already instant. Integrity is enforced in code.
SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS customers (
        customer_id  VARCHAR PRIMARY KEY,
        company_name VARCHAR NOT NULL,
        contact_name VARCHAR,
        portal_email VARCHAR NOT NULL,
        country      VARCHAR,
        is_active    BOOLEAN NOT NULL DEFAULT TRUE,
        synced_at    TIMESTAMP NOT NULL
    )
    """,
    # Only customer-safe columns exist here. internal_note, margin_pct,
    # credit_limit_usd, payment_risk and account_manager are NEVER stored.
    """
    CREATE TABLE IF NOT EXISTS orders (
        po_no                 VARCHAR PRIMARY KEY,
        customer_id           VARCHAR NOT NULL,
        style_no              VARCHAR,
        product               VARCHAR,
        colour                VARCHAR,
        qty_pcs               INTEGER,
        unit_price_usd        DOUBLE,
        order_date            DATE,
        promised_ship_date    DATE,
        revised_ship_date     DATE,
        status                VARCHAR NOT NULL,
        shipped_qty           INTEGER,
        carrier               VARCHAR,
        tracking_no           VARCHAR,
        customer_instructions VARCHAR,
        is_active             BOOLEAN NOT NULL DEFAULT TRUE,
        synced_at             TIMESTAMP NOT NULL
    )
    """,
    # Written by the app (not by sync), so a sync can never touch logins.
    """
    CREATE TABLE IF NOT EXISTS users (
        customer_id   VARCHAR PRIMARY KEY,
        email         VARCHAR NOT NULL,
        password_hash VARCHAR NOT NULL,
        created_at    TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tickets (
        ticket_id   VARCHAR PRIMARY KEY,
        customer_id VARCHAR NOT NULL,
        po_no       VARCHAR,
        message     VARCHAR NOT NULL,
        status      VARCHAR NOT NULL DEFAULT 'open',
        created_at  TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_runs (
        run_id             VARCHAR PRIMARY KEY,
        started_at         TIMESTAMP NOT NULL,
        finished_at        TIMESTAMP NOT NULL,
        status             VARCHAR NOT NULL,
        erp_calls          INTEGER,
        customers_seen     INTEGER,
        orders_seen        INTEGER,
        orders_deactivated INTEGER,
        error              VARCHAR,
        details            VARCHAR
    )
    """,
]


def _open():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        con = duckdb.connect(str(DB_PATH))
    except duckdb.IOException as exc:
        raise RuntimeError(
            f"Cannot open database {DB_PATH}. Is another process (e.g. the "
            f"server) already using it? Details: {exc}"
        ) from exc
    for stmt in SCHEMA:
        con.execute(stmt)
    return con


def get_conn():
    """Return a fresh cursor on the shared database.

    Each caller (request / thread) gets its own cursor, which is the
    thread-safe way to use DuckDB from several threads.
    """
    global _root_con
    with _lock:
        if _root_con is None:
            _root_con = _open()
        return _root_con.cursor()
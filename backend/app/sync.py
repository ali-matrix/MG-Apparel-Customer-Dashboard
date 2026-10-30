"""ERP -> DuckDB sync. Safe to re-run any time: upsert + one transaction."""
import json
import logging
import math
import sys
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone

from .db import get_conn
from .erp_client import ERPClient, ERPError

log = logging.getLogger("sync")

_sync_lock = threading.Lock()  # only one sync at a time

MAX_ORDER_PASSES = 2             # re-fetch once if data shifted while paging
MAX_DEACTIVATE_FRACTION = 0.30   # refuse mass-"deletes" (likely an ERP glitch)
MIN_ROWS_FOR_GUARD = 20

STATUS_MAP = {
    "confirmed": "Confirmed",
    "in production": "In Production",
    "ready to ship": "Ready to Ship",
    "delayed": "Delayed",
    "shipped": "Shipped",
    "cancelled": "Cancelled",
    "canceled": "Cancelled",
}

CUSTOMER_COLS = ["customer_id", "company_name", "contact_name", "portal_email", "country"]
ORDER_COLS = [
    "po_no", "customer_id", "style_no", "product", "colour", "qty_pcs",
    "unit_price_usd", "order_date", "promised_ship_date", "revised_ship_date",
    "status", "shipped_qty", "carrier", "tracking_no", "customer_instructions",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------- cleaning helpers (the ERP is not trusted to be tidy) ----------
def _s(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _to_int(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        n = int(float(v))
    except (TypeError, ValueError, OverflowError):
        return None
    return n if n >= 0 else None


def _to_float(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f >= 0 else None


def _to_date(v):
    s = _s(v)
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _parsed(raw, key, parser, stats):
    value = raw.get(key)
    out = parser(value)
    if out is None and value not in (None, ""):
        stats[f"bad_{key}"] += 1
    return out


def _clean_customer(raw, stats):
    if not isinstance(raw, dict):
        stats["skipped_customer_not_object"] += 1
        return None
    cid, name, email = _s(raw.get("customer_id")), _s(raw.get("company_name")), _s(raw.get("portal_email"))
    if not (cid and name and email):
        stats["skipped_customer_missing_fields"] += 1
        return None
    return {
        "customer_id": cid.upper(),
        "company_name": name,
        "contact_name": _s(raw.get("contact_name")),
        "portal_email": email.lower(),
        "country": _s(raw.get("country")),
    }


def _clean_order(raw, valid_customer_ids, stats):
    if not isinstance(raw, dict):
        stats["skipped_order_not_object"] += 1
        return None
    po, cid, status_raw = _s(raw.get("po_no")), _s(raw.get("customer_id")), _s(raw.get("status"))
    if not (po and cid and status_raw):
        stats["skipped_order_missing_fields"] += 1
        return None
    po, cid = po.upper(), cid.upper()
    if cid not in valid_customer_ids:
        stats["skipped_order_unknown_customer"] += 1
        return None

    key = " ".join(status_raw.replace("_", " ").replace("-", " ").lower().split())
    status = STATUS_MAP.get(key)
    if status is None:
        stats["unknown_status"] += 1
        status = status_raw

    return {
        "po_no": po,
        "customer_id": cid,
        "style_no": _s(raw.get("style_no")),
        "product": _s(raw.get("product")),
        "colour": _s(raw.get("colour")),
        "qty_pcs": _parsed(raw, "qty_pcs", _to_int, stats),
        "unit_price_usd": _parsed(raw, "unit_price_usd", _to_float, stats),
        "order_date": _parsed(raw, "order_date", _to_date, stats),
        "promised_ship_date": _parsed(raw, "promised_ship_date", _to_date, stats),
        "revised_ship_date": _parsed(raw, "revised_ship_date", _to_date, stats),
        "status": status,
        "shipped_qty": _parsed(raw, "shipped_qty", _to_int, stats),
        "carrier": _s(raw.get("carrier")),
        "tracking_no": _s(raw.get("tracking_no")),
        "customer_instructions": _s(raw.get("customer_instructions")),
    }


# ---------- SQL ----------
def _upsert_sql(table, key, cols):
    all_cols = cols + ["is_active", "synced_at"]
    placeholders = ", ".join(["?"] * len(cols)) + ", TRUE, ?"
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in all_cols if c != key)
    return (
        f"INSERT INTO {table} ({', '.join(all_cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({key}) DO UPDATE SET {updates}"
    )


CUSTOMER_UPSERT = _upsert_sql("customers", "customer_id", CUSTOMER_COLS)
ORDER_UPSERT = _upsert_sql("orders", "po_no", ORDER_COLS)


def _deactivate(con, table, now, allow):
    """Rows not touched by this sync have synced_at < now -> no longer live.
    Returns (rows_deactivated, skipped_by_guard)."""
    stale = con.execute(
        f"SELECT count(*) FROM {table} WHERE is_active AND synced_at < ?", [now]
    ).fetchone()[0]
    if stale == 0:
        return 0, False
    active = con.execute(f"SELECT count(*) FROM {table} WHERE is_active").fetchone()[0]
    if not allow:
        log.warning("%s: %d rows look gone but data was inconsistent - NOT deactivating", table, stale)
        return 0, True
    if active >= MIN_ROWS_FOR_GUARD and stale / active > MAX_DEACTIVATE_FRACTION:
        log.warning("%s: %d of %d rows would be deactivated - too many, skipping", table, stale, active)
        return 0, True
    con.execute(f"UPDATE {table} SET is_active = FALSE WHERE is_active AND synced_at < ?", [now])
    return stale, False


def _apply(con, customers, orders, now, allow_deactivate):
    """Everything in ONE transaction: all or nothing."""
    con.execute("BEGIN TRANSACTION")
    try:
        con.executemany(
            CUSTOMER_UPSERT, [[c[k] for k in CUSTOMER_COLS] + [now] for c in customers]
        )
        con.executemany(
            ORDER_UPSERT, [[o[k] for k in ORDER_COLS] + [now] for o in orders]
        )
        cust_off, cust_skip = _deactivate(con, "customers", now, allow_deactivate)
        ord_off, ord_skip = _deactivate(con, "orders", now, allow_deactivate)
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    return cust_off, ord_off, (cust_skip or ord_skip)


# ---------- orchestration ----------
def _fetch_everything(client):
    customers_raw = client.get_customers()
    consistent, orders_raw = False, []
    for attempt in range(1, MAX_ORDER_PASSES + 1):
        orders_raw, total = client.fetch_all_orders()
        unique = {
            str(o.get("po_no", "")).strip().upper()
            for o in orders_raw if isinstance(o, dict)
        }
        if len(unique) == total:
            consistent = True
            break
        log.warning(
            "Order count mismatch (pass %d): %d unique POs fetched vs ERP total_records=%d "
            "- data probably shifted while paging", attempt, len(unique), total,
        )
    return customers_raw, orders_raw, consistent


def _record_run(summary, started):
    try:
        con = get_conn()
        con.execute(
            "INSERT INTO sync_runs (run_id, started_at, finished_at, status, erp_calls, "
            "customers_seen, orders_seen, orders_deactivated, error, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                summary["run_id"], started, _utcnow(), summary["status"],
                summary.get("erp_calls"), summary.get("customers_seen"),
                summary.get("orders_seen"), summary.get("orders_deactivated"),
                summary.get("error"), json.dumps(summary.get("anomalies", {})),
            ],
        )
    except Exception as exc:  # never let bookkeeping crash the sync
        log.error("Could not record sync run: %s", exc)


def run_sync() -> dict:
    if not _sync_lock.acquire(blocking=False):
        log.info("Another sync is already running - skipping")
        return {"status": "skipped"}

    started = _utcnow()
    summary = {"run_id": uuid.uuid4().hex, "status": "failed"}
    client = None
    try:
        client = ERPClient()
        customers_raw, orders_raw, consistent = _fetch_everything(client)

        stats = Counter()
        customers = [c for c in (_clean_customer(r, stats) for r in customers_raw) if c]
        if not customers:
            raise ERPError("ERP returned no usable customers - refusing to apply")
        valid_ids = {c["customer_id"] for c in customers}

        orders_by_po = {}
        for raw in orders_raw:
            o = _clean_order(raw, valid_ids, stats)
            if o:
                if o["po_no"] in orders_by_po:
                    stats["duplicate_po_in_pages"] += 1
                orders_by_po[o["po_no"]] = o
        orders = list(orders_by_po.values())
        if orders_raw and not orders:
            raise ERPError("Every order row was rejected - refusing to apply")

        con = get_conn()
        now = _utcnow()
        cust_off, ord_off, guard_skipped = _apply(con, customers, orders, now, consistent)

        summary.update(
            customers_seen=len(customers),
            orders_seen=len(orders),
            customers_deactivated=cust_off,
            orders_deactivated=ord_off,
            anomalies=dict(stats),
            status="ok" if (consistent and not guard_skipped) else "ok_no_deactivate",
        )
        log.info("Sync finished: %s", summary)
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        log.error("Sync FAILED (previous data left untouched): %s", summary["error"])
    finally:
        summary["erp_calls"] = client.calls if client else 0
        _record_run(summary, started)
        _sync_lock.release()
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    result = run_sync()
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result["status"] in ("ok", "ok_no_deactivate") else 1)
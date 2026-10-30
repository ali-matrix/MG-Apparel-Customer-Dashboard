import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.db import get_conn  # noqa: E402

con = get_conn()
print("orders (total, unique po, active):",
      con.execute("SELECT count(*), count(DISTINCT po_no), count(*) FILTER (WHERE is_active) FROM orders").fetchone())
print("customers:", con.execute("SELECT count(*) FROM customers").fetchone()[0])
print("active orders by status:",
      con.execute("SELECT status, count(*) FROM orders WHERE is_active GROUP BY 1 ORDER BY 2 DESC").fetchall())
print("last sync runs:")
for r in con.execute(
    "SELECT started_at, status, erp_calls, orders_seen, orders_deactivated, error "
    "FROM sync_runs ORDER BY started_at DESC LIMIT 5"
).fetchall():
    print("  ", r)
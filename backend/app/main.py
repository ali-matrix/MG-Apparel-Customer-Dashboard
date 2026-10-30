"""FastAPI app: login, orders, health. Reads ONLY from the local DuckDB cache."""
import logging
import math
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import JWT_EXPIRE_MIN, JWT_SECRET, SYNC_INTERVAL_MIN
from .db import get_conn
from .chat import ChatRateLimited, ChatUnavailable, answer as chat_answer
from .security import create_token, current_customer
from .sync import run_sync
from .users import authenticate, seed_users

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------- background sync scheduler ----------------
def _last_ok_sync() -> Optional[datetime]:
    row = get_conn().execute(
        "SELECT max(finished_at) FROM sync_runs WHERE status IN ('ok', 'ok_no_deactivate')"
    ).fetchone()
    return row[0] if row else None


def _seconds_until_next_sync(failures: int) -> float:
    base = SYNC_INTERVAL_MIN * 60
    if failures:  # back off after failures so we never hammer the ERP
        return min(base * (2 ** failures), 7200)
    last = _last_ok_sync()
    if last is None:
        return 5.0
    return max(5.0, base - (_utcnow() - last).total_seconds())


def _sync_loop(stop: threading.Event):
    failures = 0
    while not stop.is_set():
        try:
            wait = _seconds_until_next_sync(failures)
            log.info("Next ERP sync in %.0f s", wait)
            if stop.wait(wait):
                break
            result = run_sync()
            if result.get("status") in ("ok", "ok_no_deactivate"):
                failures = 0
                seed_users()
            elif result.get("status") == "failed":
                failures += 1
        except Exception:
            log.exception("Sync loop error")
            failures += 1
            stop.wait(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if len(JWT_SECRET) < 32:
        raise RuntimeError("JWT_SECRET missing or too short in .env (need 32+ characters)")
    get_conn()
    try:
        seed_users()
    except Exception:
        log.exception("Could not seed users")
    stop = threading.Event()
    if SYNC_INTERVAL_MIN > 0:
        threading.Thread(target=_sync_loop, args=(stop,), daemon=True, name="erp-sync").start()
    else:
        log.info("Background sync is disabled (SYNC_INTERVAL_MIN=0)")
    yield
    stop.set()


app = FastAPI(title="MG Apparel Order Portal", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Something went wrong. Please try again."}, status_code=500)


# ---------------- models (explicit whitelist of customer-safe fields) ----------------
OrderStatus = Literal["Confirmed", "In Production", "Ready to Ship", "Delayed", "Shipped", "Cancelled"]


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class LoginOut(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    company_name: str


class MeOut(BaseModel):
    company_name: str
    contact_name: Optional[str] = None


class OrderOut(BaseModel):
    po_no: str
    style_no: Optional[str] = None
    product: Optional[str] = None
    colour: Optional[str] = None
    qty_pcs: Optional[int] = None
    order_date: Optional[date] = None
    promised_ship_date: Optional[date] = None
    revised_ship_date: Optional[date] = None
    ship_date: Optional[date] = None  # revised date if there is one, else promised
    status: str
    shipped_qty: Optional[int] = None
    carrier: Optional[str] = None
    tracking_no: Optional[str] = None
    customer_instructions: Optional[str] = None


class OrdersPage(BaseModel):
    items: List[OrderOut]
    page: int
    page_size: int
    total: int
    total_pages: int
    status_counts: Dict[str, int]
    last_synced_at: Optional[datetime] = None

class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=2000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: List[ChatTurn] = Field(default_factory=list, max_length=20)


class ChatOut(BaseModel):
    reply: str
    ticket_id: Optional[str] = None


class TicketOut(BaseModel):
    ticket_id: str
    po_no: Optional[str] = None
    message: str
    status: str
    created_at: datetime


# ---------------- login rate limiting (in memory) ----------------
_WINDOW_S, _MAX_PER_EMAIL, _MAX_PER_IP = 300, 5, 50
_fails: Dict[str, List[float]] = {}
_fails_lock = threading.Lock()


def _recent(key: str, now: float) -> List[float]:
    kept = [t for t in _fails.get(key, []) if now - t < _WINDOW_S]
    if kept:
        _fails[key] = kept
    else:
        _fails.pop(key, None)
    return kept


def _blocked(ip: str, email: str) -> bool:
    now = time.time()
    with _fails_lock:
        return (len(_recent(f"e|{ip}|{email}", now)) >= _MAX_PER_EMAIL
                or len(_recent(f"i|{ip}", now)) >= _MAX_PER_IP)


def _record_failure(ip: str, email: str):
    now = time.time()
    with _fails_lock:
        if len(_fails) > 10000:
            _fails.clear()
        for key in (f"e|{ip}|{email}", f"i|{ip}"):
            _fails.setdefault(key, []).append(now)


def _clear_failures(ip: str, email: str):
    with _fails_lock:
        _fails.pop(f"e|{ip}|{email}", None)


# ---------------- helpers ----------------
ORDER_COLUMNS = (
    "po_no, style_no, product, colour, qty_pcs, order_date, promised_ship_date, "
    "revised_ship_date, COALESCE(revised_ship_date, promised_ship_date) AS ship_date, "
    "status, shipped_qty, carrier, tracking_no, customer_instructions"
)


def _query(sql: str, params: list) -> List[dict]:
    res = get_conn().execute(sql, params)
    cols = [d[0] for d in res.description]
    return [dict(zip(cols, row)) for row in res.fetchall()]


# ---------------- routes ----------------
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/auth/login", response_model=LoginOut)
def login(body: LoginIn, request: Request):
    ip = request.client.host if request.client else "unknown"
    email = body.email.strip().lower()
    if _blocked(ip, email):
        raise HTTPException(status_code=429,
                            detail="Too many failed attempts. Please wait a few minutes and try again.")
    user = authenticate(email, body.password)
    if user is None:
        _record_failure(ip, email)
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    _clear_failures(ip, email)
    return LoginOut(access_token=create_token(user["customer_id"]), token_type="bearer",
                    expires_in=JWT_EXPIRE_MIN * 60, company_name=user["company_name"])


@app.get("/api/me", response_model=MeOut)
def whoami(me: dict = Depends(current_customer)):
    return MeOut(company_name=me["company_name"], contact_name=me["contact_name"])


@app.get("/api/orders", response_model=OrdersPage)
def list_orders(
    status: Optional[OrderStatus] = None,
    q: Optional[str] = Query(None, max_length=50),
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(25, ge=1, le=100),
    me: dict = Depends(current_customer),
):
    cid = me["customer_id"]  # from the verified token, never from the request
    where, params = ["customer_id = ?", "is_active"], [cid]
    if status:
        where.append("status = ?")
        params.append(status)
    term = (q or "").strip()
    if term:
        where.append("(po_no ILIKE ? OR style_no ILIKE ? OR product ILIKE ?)")
        params += [f"%{term}%"] * 3
    clause = " AND ".join(where)  # built only from constants; values are parameters

    total = _query(f"SELECT count(*) AS n FROM orders WHERE {clause}", params)[0]["n"]
    items = _query(
        f"SELECT {ORDER_COLUMNS} FROM orders WHERE {clause} "
        "ORDER BY order_date DESC NULLS LAST, po_no LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    )
    counts = {r["status"]: r["n"] for r in _query(
        "SELECT status, count(*) AS n FROM orders WHERE customer_id = ? AND is_active GROUP BY status",
        [cid])}
    last = _last_ok_sync()
    return OrdersPage(
        items=items, page=page, page_size=page_size, total=total,
        total_pages=max(1, math.ceil(total / page_size)), status_counts=counts,
        last_synced_at=last.replace(tzinfo=timezone.utc) if last else None,
    )


@app.get("/api/orders/{po_no}", response_model=OrderOut)
def get_order(po_no: str = Path(..., min_length=1, max_length=30),
              me: dict = Depends(current_customer)):
    rows = _query(
        f"SELECT {ORDER_COLUMNS} FROM orders WHERE po_no = ? AND customer_id = ? AND is_active",
        [po_no.strip().upper(), me["customer_id"]],
    )
    if not rows:  # someone else's PO and a non-existent PO look identical
        raise HTTPException(status_code=404, detail="Order not found.")
    return rows[0]

@app.post("/api/chat", response_model=ChatOut)
def chat(body: ChatIn, me: dict = Depends(current_customer)):
    try:
        result = chat_answer(
            me["customer_id"], me["company_name"], body.message,
            [t.model_dump() for t in body.history],
        )
    except ChatRateLimited:
        raise HTTPException(status_code=429,
                            detail="You are sending messages too quickly. Please wait a moment and try again.")
    except ChatUnavailable:
        raise HTTPException(status_code=503,
                            detail="The assistant is temporarily unavailable. Your orders are still "
                                   "available on this page. Please try again shortly.")
    return ChatOut(**result)


@app.get("/api/tickets", response_model=List[TicketOut])
def list_tickets(me: dict = Depends(current_customer)):
    return _query(
        "SELECT ticket_id, po_no, message, status, created_at FROM tickets "
        "WHERE customer_id = ? ORDER BY created_at DESC LIMIT 50",
        [me["customer_id"]],
    )
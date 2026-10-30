import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDownLeft,
  ArrowLeft,
  ArrowRight,
  Box,
  Check,
  ChevronDown,
  CircleHelp,
  LogOut,
  MessageCircle,
  PackageCheck,
  Search,
  Send,
  Truck,
  X,
} from "lucide-react";
import { apiRequest, clearToken, formatDate, getToken, saveToken } from "./api.js";

const STATUSES = [
  "Confirmed",
  "In Production",
  "Ready to Ship",
  "Delayed",
  "Shipped",
  "Cancelled",
];

const EMPTY_ORDERS = {
  items: [],
  page: 1,
  page_size: 25,
  total: 0,
  total_pages: 1,
  status_counts: {},
  last_synced_at: null,
};

function getErrorMessage(error) {
  if (error?.name === "AbortError") return "";
  return error?.message || "Something went wrong. Please try again.";
}

function App() {
  const [authenticated, setAuthenticated] = useState(Boolean(getToken()));
  const [company, setCompany] = useState("");

  const signOut = useCallback((message = "") => {
    clearToken();
    setAuthenticated(false);
    setCompany("");
    if (message) window.sessionStorage.setItem("mg-portal-notice", message);
  }, []);

  useEffect(() => {
    if (!authenticated) return undefined;
    const controller = new AbortController();
    apiRequest("/me", { signal: controller.signal })
      .then((profile) => setCompany(profile.company_name || ""))
      .catch((error) => {
        if (error.status === 401) signOut("Your session has expired. Please sign in again.");
      });
    return () => controller.abort();
  }, [authenticated, signOut]);

  if (!authenticated) {
    return (
      <LoginPage
        onLogin={(token, companyName) => {
          saveToken(token);
          setCompany(companyName);
          setAuthenticated(true);
        }}
      />
    );
  }

  return <OrdersPage company={company} onSignOut={signOut} />;
}

function BrandMark({ compact = false }) {
  return (
    <a className={`brand-mark${compact ? " brand-mark--compact" : ""}`} href="/" aria-label="MG Apparel home">
      <span className="brand-symbol">M</span>
      <span className="brand-wordmark">MG<span>APPAREL</span></span>
    </a>
  );
}

function LoginPage({ onLogin }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const savedNotice = window.sessionStorage.getItem("mg-portal-notice");
    if (savedNotice) {
      setNotice(savedNotice);
      window.sessionStorage.removeItem("mg-portal-notice");
    }
  }, []);

  async function submit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const result = await apiRequest("/auth/login", {
        method: "POST",
        body: { email: email.trim(), password },
      });
      onLogin(result.access_token, result.company_name);
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-art" aria-label="MG Apparel customer portal">
        <div className="login-art-top"><BrandMark /></div>
        <div className="login-art-content">
          <span className="eyebrow eyebrow--light">YOUR BUSINESS, IN MOTION</span>
          <h1>Every order.<br />A little closer.</h1>
          <p>One place to follow your production, shipments and delivery details.</p>
        </div>
        <div className="login-art-bottom">
          <span>MADE TO MOVE YOUR BUSINESS</span>
          <span>01 — 03</span>
        </div>
        <div className="login-art-shape login-art-shape--one" />
        <div className="login-art-shape login-art-shape--two" />
      </section>

      <section className="login-panel">
        <div className="login-panel-mobile-brand"><BrandMark compact /></div>
        <div className="login-form-wrap">
          <span className="eyebrow">CUSTOMER PORTAL</span>
          <h2>Welcome back</h2>
          <p className="login-intro">Sign in to see the latest on your orders.</p>
          {notice && <div className="notice" role="status">{notice}</div>}
          {error && <div className="alert alert--error" role="alert">{error}</div>}

          <form onSubmit={submit} className="login-form">
            <label htmlFor="email">Work email</label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              placeholder="you@company.com"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              maxLength={254}
              required
            />
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              placeholder="Enter your password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              maxLength={128}
              required
            />
            <button className="button button--primary button--wide login-submit" type="submit" disabled={busy}>
              {busy ? <span className="spinner" aria-hidden="true" /> : "Sign in"}
              {!busy && <ArrowRight size={17} aria-hidden="true" />}
            </button>
          </form>

          <div className="login-help">
            <CircleHelp size={17} aria-hidden="true" />
            <span>Need help signing in? Contact your account manager.</span>
          </div>
        </div>
        <footer className="login-footer">© MG Apparel <span>·</span> Customer order portal</footer>
      </section>
    </main>
  );
}

function OrdersPage({ company, onSignOut }) {
  const [orders, setOrders] = useState(EMPTY_ORDERS);
  const [filter, setFilter] = useState("All orders");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [chatOpen, setChatOpen] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    setPage(1);
  }, [filter, query]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ page: String(page), page_size: "25" });
    if (filter !== "All orders") params.set("status", filter);
    if (query) params.set("q", query);
    setLoading(true);
    setError("");

    apiRequest(`/orders?${params}`, { signal: controller.signal })
      .then(setOrders)
      .catch((requestError) => {
        if (requestError.name === "AbortError") return;
        if (requestError.status === 401) {
          onSignOut("Your session has expired. Please sign in again.");
          return;
        }
        setError(getErrorMessage(requestError));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [filter, onSignOut, page, query, reload]);

  const totalOrders = useMemo(
    () => Object.values(orders.status_counts || {}).reduce((total, count) => total + count, 0),
    [orders.status_counts],
  );
  const delayedCount = orders.status_counts?.Delayed || 0;
  const shippedCount = orders.status_counts?.Shipped || 0;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-inner">
          <BrandMark compact />
          <nav className="main-nav" aria-label="Main navigation">
            <a className="main-nav-link main-nav-link--active" href="#orders">My orders</a>
          </nav>
          <div className="topbar-account">
            <div className="account-avatar" aria-hidden="true">{(company || "M").slice(0, 1).toUpperCase()}</div>
            <div className="account-name"><span>Signed in as</span><strong>{company || "Customer"}</strong></div>
            <button className="icon-button signout-button" onClick={() => onSignOut()} aria-label="Sign out" title="Sign out">
              <LogOut size={18} />
            </button>
          </div>
        </div>
      </header>

      <main className="page-content" id="orders">
        <div className="welcome-row">
          <div>
            <span className="eyebrow">CUSTOMER PORTAL</span>
            <h1>Your orders<span className="title-period">.</span></h1>
            <p className="page-lede">A clear view of what’s in the works and what’s on its way.</p>
          </div>
          <div className="sync-label">
            <span className="sync-dot" />
            {orders.last_synced_at
              ? `Updated ${new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(orders.last_synced_at))}`
              : "Order updates"}
          </div>
        </div>

        <section className="summary-grid" aria-label="Order summary">
          <SummaryCard icon={<Box />} label="Total orders" value={totalOrders} />
          <SummaryCard icon={<Truck />} label="On the way" value={shippedCount} accent="green" />
          <SummaryCard icon={<ArrowDownLeft />} label="Need attention" value={delayedCount} accent="amber" />
        </section>

        <section className="orders-section" aria-labelledby="orders-heading">
          <div className="orders-section-header">
            <div>
              <h2 id="orders-heading">Order overview</h2>
              <p>Track production and delivery, all in one place.</p>
            </div>
            <label className="search-field">
              <Search size={17} aria-hidden="true" />
              <span className="visually-hidden">Search orders</span>
              <input
                type="search"
                placeholder="Search orders"
                value={search}
                onChange={(event) => setSearch(event.target.value.slice(0, 50))}
              />
              {search && <button className="search-clear" onClick={() => setSearch("")} aria-label="Clear search"><X size={15} /></button>}
            </label>
          </div>

          <div className="filter-row" role="group" aria-label="Filter orders by status">
            <FilterButton label="All orders" selected={filter === "All orders"} count={totalOrders} onClick={setFilter} />
            {STATUSES.map((status) => (
              <FilterButton
                key={status}
                label={status}
                selected={filter === status}
                count={orders.status_counts?.[status] || 0}
                onClick={setFilter}
              />
            ))}
          </div>

          {error && (
            <div className="alert alert--error orders-error" role="alert">
              <span>{error}</span>
              <button className="text-button" onClick={() => setReload((current) => current + 1)}>Try again</button>
            </div>
          )}

          <div className="orders-table-wrap">
            <div className="orders-table-head">
              <span>ORDER</span><span>STATUS</span><span>EXPECTED SHIP DATE</span><span>SHIPMENT</span><span />
            </div>
            {loading ? (
              <div className="loading-state" role="status"><span className="spinner spinner--dark" /> Loading your orders…</div>
            ) : orders.items.length ? (
              <div className="order-list">
                {orders.items.map((order) => <OrderRow key={order.po_no} order={order} />)}
              </div>
            ) : (
              <div className="empty-state">
                <div className="empty-icon"><PackageCheck size={25} /></div>
                <h3>{query || filter !== "All orders" ? "No matching orders" : "No orders to show yet"}</h3>
                <p>{query || filter !== "All orders" ? "Try another search or choose a different status." : "Your order updates will appear here as soon as they're available."}</p>
                {(query || filter !== "All orders") && <button className="text-button" onClick={() => { setSearch(""); setFilter("All orders"); }}>Clear filters</button>}
              </div>
            )}
          </div>

          <div className="table-footer">
            <span>{orders.total ? <>Showing <strong>{(page - 1) * orders.page_size + 1}–{Math.min(page * orders.page_size, orders.total)}</strong> of <strong>{orders.total}</strong> orders</> : "No orders"}</span>
            <div className="pagination">
              <button className="pagination-button" onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={page <= 1 || loading} aria-label="Previous page">
                <ArrowLeft size={16} />
              </button>
              <span>Page <strong>{page}</strong> of <strong>{orders.total_pages || 1}</strong></span>
              <button className="pagination-button" onClick={() => setPage((current) => Math.min(orders.total_pages, current + 1))} disabled={page >= orders.total_pages || loading} aria-label="Next page">
                <ArrowRight size={16} />
              </button>
            </div>
          </div>
        </section>
        <footer className="page-footer"><span>MG APPAREL</span><span>Questions about an order? We’re here to help.</span></footer>
      </main>

      <ChatWidget open={chatOpen} setOpen={setChatOpen} onSignOut={onSignOut} />
    </div>
  );
}

function SummaryCard({ icon, label, value, accent = "" }) {
  return (
    <div className="summary-card">
      <div className={`summary-icon${accent ? ` summary-icon--${accent}` : ""}`} aria-hidden="true">{icon}</div>
      <div><span className="summary-label">{label}</span><strong className="summary-value">{value}</strong></div>
    </div>
  );
}

function FilterButton({ label, count, selected, onClick }) {
  return (
    <button className={`filter-button${selected ? " filter-button--active" : ""}`} onClick={() => onClick(label)} aria-pressed={selected}>
      {label}<span>{count}</span>
    </button>
  );
}

function OrderRow({ order }) {
  const [expanded, setExpanded] = useState(false);
  const statusClass = String(order.status || "").toLowerCase().replaceAll(" ", "-");
  const shipDate = order.ship_date || order.revised_ship_date || order.promised_ship_date;
  const isRevised = Boolean(order.revised_ship_date && order.promised_ship_date && order.revised_ship_date !== order.promised_ship_date);

  return (
    <article className={`order-card${expanded ? " order-card--expanded" : ""}`}>
      <button className="order-row" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
        <div className="order-primary">
          <span className="order-icon"><PackageCheck size={19} /></span>
          <span className="order-name"><strong>{order.po_no}</strong><span>{order.product || "Order"}{order.style_no ? ` · ${order.style_no}` : ""}</span></span>
        </div>
        <div><span className={`status-pill status-pill--${statusClass}`}><span className="status-dot" />{order.status}</span></div>
        <div className="ship-date">
          <strong>{formatDate(shipDate)}</strong>
          <span>{isRevised ? "Revised date" : order.status === "Shipped" ? "Shipped" : "Expected"}</span>
        </div>
        <div className="shipment-cell">
          <span>{order.shipped_qty == null ? "—" : `${order.shipped_qty.toLocaleString()} pcs shipped`}</span>
          <span>{order.carrier || "Carrier not assigned"}</span>
        </div>
        <ChevronDown className={`row-chevron${expanded ? " row-chevron--open" : ""}`} size={18} aria-hidden="true" />
      </button>
      {expanded && (
        <div className="order-details">
          <div className="detail-item"><span>Product</span><strong>{order.product || "Not available"}</strong></div>
          <div className="detail-item"><span>Style</span><strong>{order.style_no || "Not available"}</strong></div>
          <div className="detail-item"><span>Colour</span><strong>{order.colour || "Not available"}</strong></div>
          <div className="detail-item"><span>Ordered quantity</span><strong>{order.qty_pcs == null ? "Not available" : `${order.qty_pcs.toLocaleString()} pcs`}</strong></div>
          <div className="detail-item"><span>Tracking number</span><strong className={order.tracking_no ? "tracking-number" : ""}>{order.tracking_no || "Not available yet"}</strong></div>
          {isRevised && <div className="detail-item"><span>Originally expected</span><strong>{formatDate(order.promised_ship_date)}</strong></div>}
        </div>
      )}
    </article>
  );
}

const CHAT_GREETING = {
  role: "assistant",
  text: "Hello! I can help with your order status, expected ship dates and tracking. What would you like to know?",
};

function ChatWidget({ open, setOpen, onSignOut }) {
  const [messages, setMessages] = useState([CHAT_GREETING]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const scrollRef = useRef(null);
  const inputRef = useRef(null);
  const conversation = messages.slice(1);

  useEffect(() => {
    if (!open) return;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    inputRef.current?.focus();
  }, [messages, open]);

  async function submit(event) {
    event.preventDefault();
    const message = draft.trim();
    if (!message || busy) return;
    setError("");
    setDraft("");
    const next = [...messages, { role: "user", text: message }];
    setMessages(next);
    setBusy(true);
    try {
      const result = await apiRequest("/chat", {
        method: "POST",
        body: {
          message,
          history: conversation.slice(-6).map(({ role, text }) => ({ role, text })),
        },
      });
      setMessages((current) => [...current, {
        role: "assistant",
        text: result.reply,
        ticketId: result.ticket_id,
      }]);
    } catch (requestError) {
      if (requestError.status === 401) {
        onSignOut("Your session has expired. Please sign in again.");
        return;
      }
      setError(getErrorMessage(requestError));
      setDraft(message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {!open && (
        <button className="chat-launcher" onClick={() => setOpen(true)} aria-label="Open order support chat">
          <MessageCircle size={21} />
          <span>Ask about an order</span>
          <span className="chat-launcher-dot" />
        </button>
      )}
      {open && (
        <section className="chat-panel" aria-label="Order support chat">
          <header className="chat-header">
            <div className="chat-header-icon"><MessageCircle size={19} /></div>
            <div className="chat-header-text"><strong>Order support</strong><span><i /> Here to help</span></div>
            <button className="icon-button chat-close" onClick={() => setOpen(false)} aria-label="Close chat"><X size={19} /></button>
          </header>
          <div className="chat-messages" ref={scrollRef} aria-live="polite">
            <div className="chat-date">YOUR ORDER ASSISTANT</div>
            {messages.map((item, index) => (
              <div key={`${index}-${item.role}`} className={`chat-message chat-message--${item.role}`}>
                {item.role === "assistant" && <span className="chat-avatar">M</span>}
                <div className="chat-bubble">
                  <p>{item.text}</p>
                  {item.ticketId && <span className="ticket-tag"><Check size={13} /> Request sent · {item.ticketId}</span>}
                </div>
              </div>
            ))}
            {busy && <div className="chat-message chat-message--assistant"><span className="chat-avatar">M</span><div className="chat-bubble chat-typing"><i /><i /><i /></div></div>}
          </div>
          {error && <div className="chat-error" role="alert">{error}</div>}
          <form className="chat-composer" onSubmit={submit}>
            <label className="visually-hidden" htmlFor="chat-input">Ask about your orders</label>
            <input
              id="chat-input"
              ref={inputRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value.slice(0, 1000))}
              placeholder="Ask about your orders…"
              maxLength={1000}
              disabled={busy}
            />
            <button type="submit" disabled={!draft.trim() || busy} aria-label="Send message"><Send size={17} /></button>
          </form>
          <p className="chat-disclaimer">For changes or concerns, I’ll pass a note to your account manager.</p>
        </section>
      )}
    </>
  );
}

export default App;

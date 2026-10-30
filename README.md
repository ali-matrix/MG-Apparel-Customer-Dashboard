# MG Apparel Customer Order Portal

A customer portal for checking order and shipment updates. The FastAPI backend synchronizes the ERP into a local DuckDB database. The React frontend reads the backend API; page views and chat requests do not call the ERP.

> **Development/demo setup:** This guide is for running the project on a Windows workstation. The demo accounts and default seed password are not suitable for a public production deployment.

## What is included

- Customer login with bcrypt-hashed passwords and bearer tokens.
- Customer-scoped order list, status filters, search, pagination, and order details.
- Gemini-powered order chat, with support tickets for requests that need a person.
- ERP-to-DuckDB synchronization with retries and transactional updates.
- Responsive React/Vite frontend.

## Requirements

- Windows 10 or later with PowerShell.
- Python 3.11 or later.
- Node.js LTS, which includes npm.
- Access to the MG Apparel mock ERP network and its API key.
- A Gemini API key to use the chatbot.

Check that Python, Node.js, and npm are available in a **new** PowerShell window:

```powershell
py --version
node --version
npm --version
```

If `node` or `npm` is not recognized, install Node.js LTS from [nodejs.org](https://nodejs.org/) or with:

```powershell
winget install --id OpenJS.NodeJS.LTS -e
```

After installation, close and reopen PowerShell so it picks up the updated PATH.

## 1. Configure the backend

Open PowerShell in the repository root (`D:\mg-portal` in this example):

```powershell
cd D:\mg-portal
Copy-Item .env.example .env
```

Open `.env` and set the ERP API key and a private JWT signing secret:

```powershell
notepad .env
```

Required settings:

| Variable | Value |
| --- | --- |
| `ERP_BASE_URL` | ERP base URL supplied for the interview. |
| `ERP_API_KEY` | ERP API key supplied for the interview. Keep it private. |
| `JWT_SECRET` | A randomly generated secret with at least 32 characters. |
| `GEMINI_API_KEY` | Gemini API key. Chat will be unavailable if it is blank. |

Generate a JWT secret in PowerShell, then copy the output into `.env`:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The `.env` file is ignored by Git. Never commit it or put API keys in frontend files.

Other useful settings in `.env`:

| Variable | Purpose |
| --- | --- |
| `DB_PATH` | DuckDB file location; defaults to `backend/portal.duckdb`. |
| `SYNC_INTERVAL_MIN` | Set to `0` to disable scheduled sync, or a positive number (for example `15`) to sync periodically. |
| `SEED_PASSWORD` | Initial password for customer accounts without an existing portal user. The demo default is `Portal@2026`. |
| `GEMINI_MODEL` | Gemini model name; defaults to `gemini-2.5-flash`. |

## 2. Create the Python environment

From the repository root:

```powershell
py -m venv backend\.venv
.\backend\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
```

If PowerShell blocks activation, allow script execution for this terminal only, then activate the environment:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\backend\.venv\Scripts\Activate.ps1
```

## 3. Synchronize ERP data

With the Python virtual environment active and the backend server stopped, run the initial sync from the repository root:

```powershell
python -m backend.app.sync
```

A successful run prints a summary with `"status": "ok"` or `"status": "ok_no_deactivate"`. If it reports `"failed"`, check the error, `.env` ERP settings, network access, and the sync log before starting the portal. The sync can be run again safely.

The example `.env` sets `SYNC_INTERVAL_MIN=0`, which disables automatic sync. To refresh manually later, stop the backend server, run the sync command again, then restart the backend. Alternatively, set `SYNC_INTERVAL_MIN=15` (or another positive interval) to enable periodic sync in the backend process. On first startup, scheduled sync begins shortly after the server starts.

## 4. Start the backend API

In a PowerShell window opened at the repository root, activate the environment if needed and start FastAPI:

```powershell
cd D:\mg-portal
.\backend\.venv\Scripts\Activate.ps1
python -m uvicorn backend.app.main:app --reload
```

Leave this window open. The API listens at `http://127.0.0.1:8000`. Check that it is responding in a browser at:

```text
http://127.0.0.1:8000/api/health
```

The health endpoint should return `{"status":"ok"}`. API documentation is disabled in this application.

## 5. Install and start the frontend

Open a **second** PowerShell window. Node.js/npm do not use the Python virtual environment:

```powershell
cd D:\mg-portal\frontend
npm install
npm run dev
```

Leave this window open too. Open the Vite URL printed in the terminal, usually:

```text
http://localhost:5173
```

The Vite development server proxies `/api` requests to `http://127.0.0.1:8000`. Keep both backend and frontend terminals running while using the portal. `npm run build` creates an optimized frontend build in `frontend/dist`.

## 6. Sign in

The interview/demo seed password is `Portal@2026`. Example accounts from the task instructions:

| Email | Customer |
| --- | --- |
| `erik@nordline.example` | Nordline AB |
| `hannah@kestrel.example` | Kestrel Outdoor Ltd |
| `lena@urbanthread.example` | Urbanthread GmbH |

All customers returned by the ERP can receive a seeded portal login. Users are created from each customer's `portal_email` when they do not already have a login. The password is hashed; it is not stored as plain text.

## Customer data and security

The backend derives the customer ID from the authenticated token and scopes order and ticket queries to that customer. Do not rely on frontend filtering for access control.

The backend stores a whitelist of selected customer and order fields. Staff-only ERP fields are not stored or returned to the frontend:

- `internal_note`
- `margin_pct`
- `credit_limit_usd`
- `payment_risk`
- `account_manager`

Order API responses include customer-facing order and shipment fields such as PO number, product/style/colour, quantity, dates, status, shipped quantity, carrier, and tracking number. `unit_price_usd` is stored in the local database but excluded from the order API response. The customer's own `customer_instructions` field is stored; the current UI does not display it, and the chat query does not include it in model context.

Use the portal only on a trusted development network. This repository is not configured as a hardened public production deployment: before internet-facing use, add HTTPS, production-grade secret management, appropriate hosting/network controls, and a review of the authentication/session design. Do not use the demo password for real customer accounts.

## ERP load and reliability

- Portal page loads and chat read from DuckDB; they do not request order data from the ERP.
- A full synchronization fetches customers once and orders in pages of up to 50 records. No per-order detail requests are made.
- Failed requests use bounded retries and backoff. Authentication and not-found errors are not retried.
- Sync prepares data before applying it in a transaction. Failed syncs do not intentionally replace the previous cached snapshot.
- Re-running a sync uses PO numbers as keys, so it does not create duplicate orders. Orders missing from a complete, consistent snapshot can be marked inactive.
- The ERP may return transient HTTP 503 errors. The client retries these; if all attempts fail, the sync reports failure and existing cached data remains available.
- The sync records its result and ERP call count in the local `sync_runs` table.

## Troubleshooting

### `npm` or `node` is not recognized

Install Node.js LTS, close and reopen PowerShell, then check `node --version` and `npm --version`. If using VS Code, reopening its terminal (or restarting VS Code) refreshes its PATH.

### The login page loads, but sign-in fails

Confirm the backend is running and `http://127.0.0.1:8000/api/health` responds. Check that the ERP sync succeeded and that you are using the exact test email and `Portal@2026` password. Check the backend terminal for errors.

### Login works, but there are no orders

The local database may not have been synchronized. Stop the backend, run `python -m backend.app.sync` from the repository root with the Python virtual environment active, then restart the backend.

### Chat reports that the assistant is unavailable

Set a valid `GEMINI_API_KEY` in the root `.env` and restart the backend. Orders remain available without Gemini.

### Sync fails

Check that the workstation can reach the ERP host, and verify `ERP_BASE_URL` and `ERP_API_KEY` in `.env`. Review the sync output and backend logs. A transient 503 is retried; a persistent failure should not be worked around by repeatedly hammering the ERP.

## Project layout

```text
backend/
  app/                 FastAPI API, authentication, chat, tickets, ERP client and sync
  requirements.txt     Python dependencies
frontend/
  src/                 React application and styles
  package.json         Frontend scripts and dependencies
scripts/               Local helper scripts
.env.example           Environment variable template (no credentials)
```

## Current limitations

- There is no `eval.py` in the repository yet; chatbot evaluation is not currently automated.
- The portal is a local development/demo setup, not a production deployment.

## Manager summary

Customers can sign in to a responsive portal and see their own order and shipment updates.
The portal reads from a local database, so customer visits do not add ERP requests.
Staff-only ERP fields are excluded from customer order responses, and the chatbot can create a follow-up ticket.
Before public production use, the system needs deployment hardening and automated chatbot evaluation.

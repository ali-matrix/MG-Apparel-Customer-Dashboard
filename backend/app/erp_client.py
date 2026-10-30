"""The ONLY module that talks to the ERP. Retries, backoff, call counting."""
import logging
import random
import time

import requests

from .config import (
    ERP_API_KEY, ERP_BASE_URL, ERP_MAX_ATTEMPTS, ERP_MAX_PAGES,
    ERP_PAGE_SIZE, ERP_TIMEOUT_S,
)

log = logging.getLogger("erp")


class ERPError(Exception):
    """ERP call failed (after retries, or not retryable)."""


class ERPAuthError(ERPError):
    """401/403 - wrong key. Retrying cannot help."""


class ERPNotFoundError(ERPError):
    """404."""


def _check_list_body(body):
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return "response has no 'data' list"
    return None


def _check_orders_body(body):
    problem = _check_list_body(body)
    if problem:
        return problem
    for key in ("total_pages", "total_records"):
        if not isinstance(body.get(key), int):
            return f"response is missing integer '{key}'"
    return None


def _parse_retry_after(value) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


class ERPClient:
    def __init__(self):
        if not ERP_BASE_URL or not ERP_API_KEY:
            raise ERPError("ERP_BASE_URL / ERP_API_KEY missing - check your .env")
        self.session = requests.Session()  # reuses the TCP connection
        self.session.headers.update({"X-API-Key": ERP_API_KEY})
        self.calls = 0  # every HTTP attempt is counted (the ERP logs them all)

    def _get(self, path, params=None, check=None):
        url = f"{ERP_BASE_URL}{path}"
        last_error = "unknown error"

        for attempt in range(1, ERP_MAX_ATTEMPTS + 1):
            retry_after = 0.0
            self.calls += 1
            try:
                resp = self.session.get(url, params=params, timeout=ERP_TIMEOUT_S)
            except requests.RequestException as exc:  # timeout, connection, etc.
                last_error = f"network error ({type(exc).__name__})"
            else:
                code = resp.status_code
                if code == 200:
                    try:
                        body = resp.json()
                    except ValueError:
                        last_error = "HTTP 200 but body is not valid JSON"
                    else:
                        problem = check(body) if check else None
                        if problem is None:
                            return body
                        last_error = f"HTTP 200 but bad body: {problem}"
                elif code in (401, 403):
                    raise ERPAuthError(f"ERP rejected the API key (HTTP {code})")
                elif code == 404:
                    raise ERPNotFoundError(f"{path} not found (HTTP 404)")
                elif code == 429 or code >= 500:
                    last_error = f"HTTP {code}"
                    retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                else:
                    raise ERPError(f"Unexpected HTTP {code} for {path}")

            if attempt < ERP_MAX_ATTEMPTS:
                # exponential backoff 1s, 2s, 4s... + jitter; obey Retry-After
                delay = min(2 ** (attempt - 1), 30) + random.uniform(0, 0.5)
                delay = max(delay, min(retry_after, 60.0))
                log.warning(
                    "ERP %s %s failed (%s) - attempt %d/%d, retrying in %.1fs",
                    path, params or {}, last_error, attempt, ERP_MAX_ATTEMPTS, delay,
                )
                time.sleep(delay)

        raise ERPError(f"{path} failed after {ERP_MAX_ATTEMPTS} attempts: {last_error}")

    def get_customers(self) -> list:
        return self._get("/v1/customers", check=_check_list_body)["data"]

    def get_orders_page(self, page: int) -> dict:
        return self._get(
            "/v1/orders",
            params={"page": page, "page_size": ERP_PAGE_SIZE},
            check=_check_orders_body,
        )

    def fetch_all_orders(self):
        """Walk every page. Returns (rows, total_records_reported_by_last_page)."""
        rows, page, total_pages, total_records = [], 1, 1, 0
        while page <= total_pages:
            if page > ERP_MAX_PAGES:
                raise ERPError(f"More than {ERP_MAX_PAGES} pages - aborting")
            body = self.get_orders_page(page)
            rows.extend(body["data"])
            total_pages = body["total_pages"]      # follow the ERP if it grows
            total_records = body["total_records"]
            page += 1
        return rows, total_records
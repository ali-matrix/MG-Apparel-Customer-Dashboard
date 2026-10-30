"""Password hashing, JWT tokens and the 'who is logged in' dependency."""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import JWT_EXPIRE_MIN, JWT_SECRET
from .db import get_conn

ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)


def _pw_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:72]  # bcrypt only uses the first 72 bytes


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_pw_bytes(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_pw_bytes(password), hashed.encode("ascii"))
    except ValueError:
        return False


# Used so that "unknown email" takes as long as "wrong password" (no user enumeration)
DUMMY_HASH = hash_password("not-a-real-password")


def create_token(customer_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": customer_id, "iat": now, "exp": now + timedelta(minutes=JWT_EXPIRE_MIN)}
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def _decode(token: str) -> Optional[str]:
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM],
                          options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None
    sub = data.get("sub")
    return sub if isinstance(sub, str) else None


def _unauthorized(msg: str) -> HTTPException:
    return HTTPException(status_code=401, detail=msg,
                         headers={"WWW-Authenticate": "Bearer"})


def current_customer(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> dict:
    """The ONLY source of customer_id for every protected endpoint."""
    if creds is None:
        raise _unauthorized("Not authenticated.")
    customer_id = _decode(creds.credentials)
    if not customer_id:
        raise _unauthorized("Your session has expired. Please log in again.")
    row = get_conn().execute(
        "SELECT customer_id, company_name, contact_name FROM customers "
        "WHERE customer_id = ? AND is_active",
        [customer_id],
    ).fetchone()
    if row is None:
        raise _unauthorized("Your session has expired. Please log in again.")
    return {"customer_id": row[0], "company_name": row[1], "contact_name": row[2]}
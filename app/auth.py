import hashlib
import hmac
import os
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from . import models

ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/admin/login")
# customer token; auto_error=False so endpoints can allow guests too
user_oauth2 = OAuth2PasswordBearer(tokenUrl="api/auth/login", auto_error=False)


# ---------- password hashing (stdlib pbkdf2, no extra deps) ----------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def _get_settings_row(db: Session):
    # local import avoids circular import with store
    from .store import get_settings
    return get_settings(db)


def get_admin_username(db: Session) -> str:
    row = _get_settings_row(db)
    return row.admin_username or settings.ADMIN_USERNAME


def create_access_token(subject: str, role: str = "admin") -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": subject, "role": role, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def verify_credentials(db: Session, username: str, password: str) -> bool:
    row = _get_settings_row(db)
    # use DB creds if set, else fall back to .env
    if row.admin_username and row.admin_password_hash:
        return username == row.admin_username and verify_password(password, row.admin_password_hash)
    return username == settings.ADMIN_USERNAME and password == settings.ADMIN_PASSWORD


def require_admin(token: str = Depends(oauth2_scheme)) -> str:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        # only admin-role tokens may access admin endpoints
        if not sub or payload.get("role") != "admin":
            raise cred_exc
        return sub
    except JWTError:
        raise cred_exc


def _user_from_token(token, db: Session, required: bool):
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Please log in to continue",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        if required:
            raise cred_exc
        return None
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("role") != "user":
            raise cred_exc
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        if required:
            raise cred_exc
        return None
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user and required:
        raise cred_exc
    return user


def require_user(token: str = Depends(user_oauth2), db: Session = Depends(get_db)):
    """Logged-in customer (raises 401 if not)."""
    return _user_from_token(token, db, required=True)


def optional_user(token: str = Depends(user_oauth2), db: Session = Depends(get_db)):
    """Current customer if logged in, else None (no error)."""
    return _user_from_token(token, db, required=False)

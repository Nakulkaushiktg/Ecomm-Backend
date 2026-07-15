import secrets
import time
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import (
    hash_password, verify_password, create_access_token, require_user,
)
from ..notify import send_reset_otp
from .. import models, schemas

router = APIRouter(prefix="/api/auth", tags=["auth"])

# simple in-memory rate limiter (per IP + action) to slow brute-force attempts
_rate_hits: dict[str, list[float]] = {}


def _rate_limit(request: Request, action: str, max_calls: int, window_sec: int):
    ip = request.client.host if request.client else "unknown"
    key = f"{action}:{ip}"
    now = time.time()
    hits = [t for t in _rate_hits.get(key, []) if now - t < window_sec]
    if len(hits) >= max_calls:
        raise HTTPException(429, "Too many attempts. Please wait a few minutes and try again.")
    hits.append(now)
    _rate_hits[key] = hits


def _auth_response(user: models.User) -> schemas.AuthResponse:
    token = create_access_token(str(user.id), role="user")
    return schemas.AuthResponse(access_token=token, user=user)


@router.post("/register", response_model=schemas.AuthResponse)
def register(payload: schemas.UserRegister, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "A valid email is required")
    if len(payload.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    if not payload.name.strip():
        raise HTTPException(400, "Name is required")
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(400, "An account with this email already exists")
    user = models.User(
        name=payload.name.strip(),
        email=email,
        phone=payload.phone.strip(),
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _auth_response(user)


@router.post("/login", response_model=schemas.AuthResponse)
def login(payload: schemas.UserLogin, request: Request, db: Session = Depends(get_db)):
    _rate_limit(request, "login", max_calls=8, window_sec=300)
    email = payload.email.strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return _auth_response(user)


@router.post("/forgot")
def forgot_password(payload: schemas.ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)):
    """Customer forgot password: generate a 6-digit OTP, store it hashed with a
    10-minute expiry, and email it to the customer."""
    _rate_limit(request, "forgot", max_calls=4, window_sec=600)
    email = payload.email.strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(404, "No account found with this email. Please check or sign up.")

    otp = "%06d" % secrets.randbelow(1_000_000)
    user.reset_otp = hash_password(otp)
    user.reset_otp_expiry = datetime.utcnow() + timedelta(minutes=10)
    user.reset_requested = True
    db.commit()

    try:
        send_reset_otp(user.email, otp, user.name)
    except Exception as e:
        print("[forgot] otp email failed:", e)
        raise HTTPException(502, "Could not send the reset code right now. Please try again shortly.")
    return {"ok": True}


@router.post("/reset")
def reset_password(payload: schemas.ResetPasswordRequest, request: Request, db: Session = Depends(get_db)):
    """Verify the emailed OTP and set a new password."""
    _rate_limit(request, "reset", max_calls=10, window_sec=600)
    email = payload.email.strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not user.reset_otp or not user.reset_otp_expiry:
        raise HTTPException(400, "No reset request found. Please request a new code.")
    if datetime.utcnow() > user.reset_otp_expiry:
        raise HTTPException(400, "This code has expired. Please request a new one.")
    if not verify_password(payload.otp.strip(), user.reset_otp):
        raise HTTPException(400, "Incorrect code. Please check and try again.")
    if len(payload.new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    user.password_hash = hash_password(payload.new_password)
    user.reset_otp = ""
    user.reset_otp_expiry = None
    user.reset_requested = False
    db.commit()
    return {"ok": True}


@router.get("/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(require_user)):
    return user


@router.put("/me", response_model=schemas.UserOut)
def update_me(
    payload: schemas.UserProfileUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_user),
):
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(user, k, v)
    db.commit()
    db.refresh(user)
    return user


@router.get("/orders", response_model=List[schemas.OrderOut])
def my_orders(
    db: Session = Depends(get_db),
    user: models.User = Depends(require_user),
):
    return (
        db.query(models.Order)
        .filter(models.Order.user_id == user.id)
        .order_by(models.Order.created_at.desc())
        .all()
    )


# ---------- Wishlist (per customer) ----------
@router.get("/wishlist", response_model=List[int])
def get_wishlist(db: Session = Depends(get_db), user: models.User = Depends(require_user)):
    rows = (
        db.query(models.WishlistItem.product_id)
        .filter(models.WishlistItem.user_id == user.id)
        .all()
    )
    return [r[0] for r in rows]


@router.post("/wishlist/{product_id}")
def add_wishlist(
    product_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_user),
):
    if not db.query(models.Product.id).filter(models.Product.id == product_id).first():
        raise HTTPException(404, "Product not found")
    exists = (
        db.query(models.WishlistItem)
        .filter(
            models.WishlistItem.user_id == user.id,
            models.WishlistItem.product_id == product_id,
        )
        .first()
    )
    if not exists:
        db.add(models.WishlistItem(user_id=user.id, product_id=product_id))
        db.commit()
    return {"ok": True}


@router.delete("/wishlist/{product_id}")
def remove_wishlist(
    product_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_user),
):
    db.query(models.WishlistItem).filter(
        models.WishlistItem.user_id == user.id,
        models.WishlistItem.product_id == product_id,
    ).delete()
    db.commit()
    return {"ok": True}

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import (
    hash_password, verify_password, create_access_token, require_user,
)
from ..notify import notify_password_reset
from .. import models, schemas

router = APIRouter(prefix="/api/auth", tags=["auth"])


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
def login(payload: schemas.UserLogin, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return _auth_response(user)


@router.post("/forgot")
def forgot_password(payload: schemas.ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Customer forgot password: flag the account and notify the store owner.
    Always returns ok (don't reveal which emails are registered)."""
    email = payload.email.strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(404, "No account found with this email. Please check or sign up.")
    user.reset_requested = True
    db.commit()
    try:
        notify_password_reset(user)
    except Exception as e:
        print("[forgot] notify failed:", e)
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

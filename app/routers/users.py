from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import (
    hash_password, verify_password, create_access_token, require_user,
)
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

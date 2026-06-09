from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import (
    verify_credentials, create_access_token, require_admin,
    hash_password, verify_password,
)
from ..utils import slugify
from ..store import get_settings
from ..notify import send_customer_email
from .. import models, schemas

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------- Customers ----------
@router.get("/customers", response_model=List[schemas.AdminCustomerOut])
def list_customers(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    users = db.query(models.User).order_by(models.User.created_at.desc()).all()
    counts = dict(
        db.query(models.Order.user_id, func.count(models.Order.id))
        .filter(models.Order.user_id.isnot(None))
        .group_by(models.Order.user_id)
        .all()
    )
    out = []
    for u in users:
        item = schemas.AdminCustomerOut.model_validate(u)
        item.order_count = counts.get(u.id, 0)
        out.append(item)
    return out


@router.put("/customers/{user_id}/reset-password")
def reset_customer_password(
    user_id: int,
    payload: schemas.AdminResetPassword,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    if len(payload.new_password) < 6:
        raise HTTPException(400, "New password must be at least 6 characters")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Customer not found")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True, "email": user.email}


@router.post("/login", response_model=schemas.TokenResponse)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    if not verify_credentials(db, payload.username, payload.password):
        raise HTTPException(401, "Invalid username or password")
    return schemas.TokenResponse(access_token=create_access_token(payload.username))


@router.put("/credentials")
def change_credentials(
    payload: schemas.CredentialsUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    cfg = get_settings(db)
    # verify current password
    if not verify_credentials(db, payload.current_username, payload.current_password):
        raise HTTPException(400, "Current username or password is incorrect")
    if payload.new_username:
        cfg.admin_username = payload.new_username.strip()
    elif not cfg.admin_username:
        cfg.admin_username = payload.current_username
    if payload.new_password:
        if len(payload.new_password) < 4:
            raise HTTPException(400, "New password must be at least 4 characters")
        cfg.admin_password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True, "username": cfg.admin_username}


def _unique_slug(db: Session, name: str, exclude_id: Optional[int] = None) -> str:
    base = slugify(name)
    slug = base
    i = 2
    while True:
        q = db.query(models.Product).filter(models.Product.slug == slug)
        if exclude_id:
            q = q.filter(models.Product.id != exclude_id)
        if not q.first():
            return slug
        slug = f"{base}-{i}"
        i += 1


# ---------- Products CRUD ----------
@router.get("/products", response_model=List[schemas.ProductOut])
def admin_list_products(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    return db.query(models.Product).order_by(models.Product.created_at.desc()).all()


def _apply_variants(product, variant_list):
    """Replace a product's variants and sync total stock = sum of variant stock."""
    product.variants.clear()
    total = 0
    for v in variant_list:
        product.variants.append(
            models.ProductVariant(
                size=v.size, color=v.color, stock=max(0, v.stock),
                price=max(0, v.price), mrp=max(0, v.mrp),
            )
        )
        total += max(0, v.stock)
    if variant_list:
        product.stock = total  # keep product.stock as the sum for variant products


@router.post("/products", response_model=schemas.ProductOut)
def create_product(
    payload: schemas.ProductCreate,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    data = payload.model_dump()
    variants = data.pop("variants", [])
    product = models.Product(**data)
    product.slug = _unique_slug(db, payload.name)
    _apply_variants(product, payload.variants)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.put("/products/{product_id}", response_model=schemas.ProductOut)
def update_product(
    product_id: int,
    payload: schemas.ProductUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(404, "Product not found")
    data = payload.model_dump(exclude_unset=True)
    variants = data.pop("variants", None)
    if "name" in data and data["name"]:
        product.slug = _unique_slug(db, data["name"], exclude_id=product_id)
    for k, v in data.items():
        setattr(product, k, v)
    if variants is not None:
        _apply_variants(product, payload.variants)
    db.commit()
    db.refresh(product)
    return product


@router.delete("/products/{product_id}")
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(404, "Product not found")
    db.delete(product)
    db.commit()
    return {"ok": True}


# ---------- Orders ----------
@router.get("/orders", response_model=List[schemas.OrderOut])
def admin_list_orders(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    q = db.query(models.Order)
    if status:
        q = q.filter(models.Order.status == status)
    return q.order_by(models.Order.created_at.desc()).all()


@router.get("/orders/{order_id}", response_model=schemas.OrderOut)
def admin_get_order(
    order_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(404, "Order not found")
    return order


@router.delete("/orders/{order_id}")
def delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(404, "Order not found")
    db.delete(order)  # cascade removes order items
    db.commit()
    return {"ok": True}


@router.put("/orders/{order_id}/status", response_model=schemas.OrderOut)
def update_order_status(
    order_id: int,
    payload: schemas.OrderStatusUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    valid = {"pending", "paid", "shipped", "delivered", "cancelled"}
    if payload.status not in valid:
        raise HTTPException(400, f"Status must be one of {valid}")
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(404, "Order not found")
    order.status = payload.status
    db.commit()
    db.refresh(order)
    return order


@router.put("/orders/{order_id}/shipment", response_model=schemas.OrderOut)
def update_shipment(
    order_id: int,
    payload: schemas.ShipmentUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(404, "Order not found")
    order.courier = payload.courier
    order.tracking_id = payload.tracking_id
    if order.status in ("pending", "paid"):
        order.status = "shipped"
    db.commit()
    db.refresh(order)
    return order


@router.post("/orders/{order_id}/email")
def email_customer(
    order_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(404, "Order not found")
    if not order.email:
        raise HTTPException(400, "This customer did not provide an email address")
    try:
        send_customer_email(order)
    except Exception as e:
        raise HTTPException(502, f"Could not send email: {e}")
    return {"ok": True, "sent_to": order.email}


# ---------- Settings (COD toggle, shipping rates) ----------
@router.get("/settings", response_model=schemas.SettingsOut)
def get_store_settings(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    return get_settings(db)


@router.put("/settings", response_model=schemas.SettingsOut)
def update_store_settings(
    payload: schemas.SettingsUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    cfg = get_settings(db)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(cfg, k, v)
    db.commit()
    db.refresh(cfg)
    return cfg


# ---------- Coupons CRUD ----------
@router.get("/coupons", response_model=List[schemas.CouponOut])
def list_coupons(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    return db.query(models.Coupon).order_by(models.Coupon.created_at.desc()).all()


@router.post("/coupons", response_model=schemas.CouponOut)
def create_coupon(
    payload: schemas.CouponCreate,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    code = payload.code.strip().upper()
    if db.query(models.Coupon).filter(models.Coupon.code == code).first():
        raise HTTPException(400, "Coupon code already exists")
    coupon = models.Coupon(
        code=code,
        discount_percent=payload.discount_percent,
        min_order=payload.min_order,
        is_active=payload.is_active,
    )
    db.add(coupon)
    db.commit()
    db.refresh(coupon)
    return coupon


@router.put("/coupons/{coupon_id}", response_model=schemas.CouponOut)
def update_coupon(
    coupon_id: int,
    payload: schemas.CouponCreate,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    coupon = db.query(models.Coupon).filter(models.Coupon.id == coupon_id).first()
    if not coupon:
        raise HTTPException(404, "Coupon not found")
    coupon.code = payload.code.strip().upper()
    coupon.discount_percent = payload.discount_percent
    coupon.min_order = payload.min_order
    coupon.is_active = payload.is_active
    db.commit()
    db.refresh(coupon)
    return coupon


@router.delete("/coupons/{coupon_id}")
def delete_coupon(
    coupon_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    coupon = db.query(models.Coupon).filter(models.Coupon.id == coupon_id).first()
    if not coupon:
        raise HTTPException(404, "Coupon not found")
    db.delete(coupon)
    db.commit()
    return {"ok": True}


# ---------- Categories CRUD ----------
@router.get("/categories", response_model=List[schemas.CategoryOut])
def admin_list_categories(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    return db.query(models.Category).order_by(
        models.Category.sort_order, models.Category.id
    ).all()


@router.post("/categories", response_model=schemas.CategoryOut)
def create_category(
    payload: schemas.CategoryCreate,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    key = (payload.key or slugify(payload.label)).strip().lower()
    if db.query(models.Category).filter(models.Category.key == key).first():
        raise HTTPException(400, "A category with this key already exists")
    cat = models.Category(
        key=key, label=payload.label, emoji=payload.emoji or "🧶", image=payload.image,
        is_active=payload.is_active, sort_order=payload.sort_order,
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


@router.put("/categories/{cat_id}", response_model=schemas.CategoryOut)
def update_category(
    cat_id: int,
    payload: schemas.CategoryUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    cat = db.query(models.Category).filter(models.Category.id == cat_id).first()
    if not cat:
        raise HTTPException(404, "Category not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(cat, k, v)
    db.commit()
    db.refresh(cat)
    return cat


@router.delete("/categories/{cat_id}")
def delete_category(
    cat_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    cat = db.query(models.Category).filter(models.Category.id == cat_id).first()
    if not cat:
        raise HTTPException(404, "Category not found")
    in_use = db.query(models.Product).filter(models.Product.category == cat.key).count()
    if in_use:
        raise HTTPException(
            400, f"{in_use} product(s) use this category. Move/delete them or set the category inactive instead."
        )
    db.delete(cat)
    db.commit()
    return {"ok": True}


# ---------- Reviews moderation ----------
@router.get("/reviews", response_model=List[schemas.ReviewOut])
def list_all_reviews(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    return db.query(models.Review).order_by(models.Review.created_at.desc()).all()


@router.delete("/reviews/{review_id}")
def delete_review(
    review_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    r = db.query(models.Review).filter(models.Review.id == review_id).first()
    if not r:
        raise HTTPException(404, "Review not found")
    db.delete(r)
    db.commit()
    return {"ok": True}

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas

router = APIRouter(prefix="/api/products", tags=["products"])


def _attach_ratings(db: Session, products):
    if not products:
        return products
    ids = [p.id for p in products]
    rows = (
        db.query(
            models.Review.product_id,
            func.avg(models.Review.rating),
            func.count(models.Review.id),
        )
        .filter(models.Review.product_id.in_(ids))
        .group_by(models.Review.product_id)
        .all()
    )
    agg = {pid: (round(float(avg), 1), cnt) for pid, avg, cnt in rows}
    for p in products:
        avg, cnt = agg.get(p.id, (0, 0))
        p.avg_rating = avg
        p.review_count = cnt
    return products


@router.get("", response_model=List[schemas.ProductOut])
def list_products(
    category: Optional[str] = None,
    featured: Optional[bool] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(models.Product).filter(models.Product.is_active == True)  # noqa: E712
    if category:
        q = q.filter(models.Product.category == category)
    if featured is not None:
        q = q.filter(models.Product.is_featured == featured)
    if search:
        like = f"%{search}%"
        q = q.filter(
            models.Product.name.ilike(like) | models.Product.description.ilike(like)
        )
    products = q.order_by(models.Product.created_at.desc()).all()
    return _attach_ratings(db, products)


@router.get("/{slug}", response_model=schemas.ProductOut)
def get_product(slug: str, db: Session = Depends(get_db)):
    p = db.query(models.Product).filter(models.Product.slug == slug).first()
    if not p:
        raise HTTPException(404, "Product not found")
    return _attach_ratings(db, [p])[0]


@router.get("/{slug}/reviews", response_model=List[schemas.ReviewOut])
def list_reviews(slug: str, db: Session = Depends(get_db)):
    p = db.query(models.Product).filter(models.Product.slug == slug).first()
    if not p:
        raise HTTPException(404, "Product not found")
    return (
        db.query(models.Review)
        .filter(models.Review.product_id == p.id)
        .order_by(models.Review.created_at.desc())
        .all()
    )


@router.post("/{slug}/reviews", response_model=schemas.ReviewOut)
def add_review(slug: str, payload: schemas.ReviewCreate, db: Session = Depends(get_db)):
    p = db.query(models.Product).filter(models.Product.slug == slug).first()
    if not p:
        raise HTTPException(404, "Product not found")
    rating = min(5, max(1, payload.rating))
    review = models.Review(
        product_id=p.id,
        name=payload.name.strip() or "Anonymous",
        rating=rating,
        comment=payload.comment.strip(),
        image_url=payload.image_url.strip(),
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


@router.get("/{slug}/related", response_model=List[schemas.ProductOut])
def related_products(slug: str, db: Session = Depends(get_db)):
    p = db.query(models.Product).filter(models.Product.slug == slug).first()
    if not p:
        raise HTTPException(404, "Product not found")
    items = (
        db.query(models.Product)
        .filter(
            models.Product.is_active == True,  # noqa: E712
            models.Product.category == p.category,
            models.Product.id != p.id,
        )
        .order_by(models.Product.is_bestseller.desc(), models.Product.created_at.desc())
        .limit(4)
        .all()
    )
    return _attach_ratings(db, items)

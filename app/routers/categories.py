from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas

router = APIRouter(prefix="/api/categories", tags=["categories"])

DEFAULTS = [
    ("woolen", "Woolen Handmade", "🧶", 1),
    ("god", "God & Spiritual", "🪔", 2),
    ("jewellery", "Sacred Jewellery", "📿", 3),
    ("clothes", "Cotton & Woolen Clothes", "👗", 4),
]


def seed_if_empty(db: Session):
    if db.query(models.Category).count() == 0:
        for key, label, emoji, order in DEFAULTS:
            db.add(models.Category(key=key, label=label, emoji=emoji, sort_order=order))
        db.commit()


@router.get("", response_model=List[schemas.CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    seed_if_empty(db)
    return (
        db.query(models.Category)
        .filter(models.Category.is_active == True)  # noqa: E712
        .order_by(models.Category.sort_order, models.Category.id)
        .all()
    )

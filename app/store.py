from sqlalchemy.orm import Session

from . import models
from .config import settings as env


def get_settings(db: Session) -> models.Setting:
    """Return the single settings row, creating it from .env defaults if absent."""
    row = db.query(models.Setting).filter(models.Setting.id == 1).first()
    if not row:
        row = models.Setting(
            id=1,
            enable_cod=env.ENABLE_COD,
            shipping_per_500g=env.SHIPPING_PER_500G,
            free_shipping_above=env.FREE_SHIPPING_ABOVE,
            cod_fee=env.COD_FEE,
            admin_username=env.ADMIN_USERNAME,
            admin_password_hash="",  # empty = fall back to .env password until changed
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row

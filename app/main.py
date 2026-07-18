import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from sqlalchemy import text

from .config import settings
from .database import Base, engine
from .routers import products, orders, admin, upload, categories, users

# create tables
Base.metadata.create_all(bind=engine)


def _run_migrations():
    """Add new columns to existing tables (create_all won't alter them).
    Idempotent — safe to run on every startup (Postgres)."""
    stmts = [
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS is_new BOOLEAN DEFAULT FALSE",
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS is_trending BOOLEAN DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_otp VARCHAR(200) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_otp_expiry TIMESTAMP",
        "ALTER TABLE settings ADD COLUMN IF NOT EXISTS instagram_url VARCHAR(200) DEFAULT ''",
        "ALTER TABLE reviews ADD COLUMN IF NOT EXISTS show_on_site BOOLEAN DEFAULT TRUE",
        "ALTER TABLE settings ADD COLUMN IF NOT EXISTS banner_start VARCHAR(40) DEFAULT ''",
        "ALTER TABLE settings ADD COLUMN IF NOT EXISTS banner_end VARCHAR(40) DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_number VARCHAR(20)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS points INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS gift_pending BOOLEAN DEFAULT FALSE",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS gift_claimed BOOLEAN DEFAULT FALSE",
        "ALTER TABLE settings ADD COLUMN IF NOT EXISTS show_stats BOOLEAN DEFAULT TRUE",
        "ALTER TABLE settings ADD COLUMN IF NOT EXISTS show_loyalty BOOLEAN DEFAULT TRUE",
        "ALTER TABLE settings ADD COLUMN IF NOT EXISTS stat_orders VARCHAR(20) DEFAULT '500'",
        "ALTER TABLE settings ADD COLUMN IF NOT EXISTS stat_designs VARCHAR(20) DEFAULT '50'",
    ]
    with engine.begin() as conn:
        for s in stmts:
            try:
                conn.execute(text(s))
            except Exception as e:  # pragma: no cover
                print("[migrate] skip:", e)


_run_migrations()

app = FastAPI(title="Kirti Thread Art API")

# allow the configured frontend, localhost, and any *.vercel.app preview/prod
_origins = [settings.FRONTEND_ORIGIN, "http://localhost:5173", "http://127.0.0.1:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in _origins if o],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# serve uploaded images
UPLOAD_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
os.makedirs(os.path.join(UPLOAD_ROOT, "products"), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_ROOT, "qr"), exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_ROOT), name="uploads")

app.include_router(products.router)
app.include_router(orders.router)
app.include_router(admin.router)
app.include_router(upload.router)
app.include_router(upload.public_router)
app.include_router(categories.router)
app.include_router(users.router)


@app.get("/api/health")
def health():
    # touch the DB so a single keep-alive ping also keeps Neon (free tier) awake,
    # otherwise the first real query after idle is slow even when the app is warm
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        db_ok = False
        print("[health] db ping failed:", e)
    return {"status": "ok", "db": db_ok}

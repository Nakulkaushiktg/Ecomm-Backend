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
    return {"status": "ok"}

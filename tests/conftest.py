import os
import tempfile

# Configure BEFORE the app is imported: throwaway SQLite DB, no real email /
# WhatsApp / Cloudinary, fake Razorpay keys. Env vars beat the .env file.
_db = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update({
    # set TEST_DATABASE_URL to run the suite on a throwaway Postgres DB instead
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_db}",
    "SECRET_KEY": "test-secret",
    "NOTIFY_PROVIDER": "none",
    "EMAIL": "", "EMAIL_PASSWORD": "", "BREVO_API_KEY": "", "BREVO_SENDER": "",
    "RESEND_API_KEY": "",
    "CLOUDINARY_CLOUD_NAME": "", "CLOUDINARY_API_KEY": "", "CLOUDINARY_API_SECRET": "",
    "RAZORPAY_KEY_ID": "rzp_test_key",
    "RAZORPAY_KEY_SECRET": "rzp_test_secret",
    "RAZORPAY_WEBHOOK_SECRET": "whsec_test",
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD": "admin-pass-123",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main, models, ratelimit  # noqa: E402
from app.auth import create_access_token, hash_password  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    main._run_migrations()
    ratelimit.reset()
    yield


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {create_access_token('admin', role='admin')}"}


def make_product(db, **kw):
    data = dict(
        name="Shawl", slug=kw.pop("slug", "shawl"), category="woolen",
        price=1200, stock=10, weight_grams=500,
    )
    data.update(kw)
    p = models.Product(**data)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def make_user(db, points=0, email="a@example.com"):
    u = models.User(name="Asha", email=email, phone="9876543210",
                    password_hash=hash_password("secret123"), points=points)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def user_headers(user):
    return {"Authorization": f"Bearer {create_access_token(str(user.id), role='user')}"}


CUSTOMER = dict(
    customer_name="Asha", phone="+91 98765 43210", email="a@example.com",
    address="12 MG Road", city="Delhi", state="Delhi", pincode="110001",
)

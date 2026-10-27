from .conftest import make_user, user_headers


def test_login_rate_limit_is_per_visitor_not_global(client, db):
    make_user(db)
    a = {"X-Forwarded-For": "1.1.1.1, 76.76.21.21"}
    b = {"X-Forwarded-For": "2.2.2.2, 76.76.21.21"}
    bad = {"email": "a@example.com", "password": "wrong"}
    for _ in range(8):
        assert client.post("/api/auth/login", json=bad, headers=a).status_code == 401
    assert client.post("/api/auth/login", json=bad, headers=a).status_code == 429
    # another visitor behind the same proxy is NOT blocked
    ok = client.post("/api/auth/login", json={"email": "a@example.com", "password": "secret123"}, headers=b)
    assert ok.status_code == 200


def test_admin_login_is_rate_limited(client):
    h = {"X-Forwarded-For": "3.3.3.3"}
    for _ in range(5):
        r = client.post("/api/admin/login", json={"username": "admin", "password": "nope"}, headers=h)
        assert r.status_code == 401
    r = client.post("/api/admin/login", json={"username": "admin", "password": "admin-pass-123"}, headers=h)
    assert r.status_code == 429


def test_admin_login_works(client):
    r = client.post("/api/admin/login", json={"username": "admin", "password": "admin-pass-123"})
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_admin_new_password_min_length(client, admin_headers):
    r = client.put("/api/admin/credentials", headers=admin_headers, json={
        "current_username": "admin", "current_password": "admin-pass-123", "new_password": "short",
    })
    assert r.status_code == 400


def test_review_upload_requires_login(client, db):
    files = {"file": ("x.png", b"\x89PNG fake", "image/png")}
    assert client.post("/api/upload/review", files=files).status_code == 401


def test_review_upload_rejects_video(client, db):
    user = make_user(db)
    files = {"file": ("x.mp4", b"fake", "video/mp4")}
    r = client.post("/api/upload/review", files=files, headers=user_headers(user))
    assert r.status_code == 400


def test_store_config_exposes_whatsapp(client):
    r = client.get("/api/orders/config")
    assert r.status_code == 200
    assert r.json()["owner_whatsapp"]

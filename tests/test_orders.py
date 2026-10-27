import hashlib
import hmac
import json

import pytest

from app import models, razorpay_client
from app.notify import _build_customer_html, _build_html

from .conftest import CUSTOMER, make_product, make_user, user_headers


def _cod_order(client, product, qty=1, headers=None, **extra):
    body = dict(CUSTOMER, payment_method="cod", items=[{"product_id": product.id, "quantity": qty}])
    body.update(extra)
    r = client.post("/api/orders", json=body, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()["order"]


# ---------------- order tracking (PII leak) ----------------

def test_track_requires_matching_full_phone(client, db):
    p = make_product(db)
    order = _cod_order(client, p)
    num = order["order_number"]

    ok = client.post("/api/orders/track", json={"order_id": num, "phone": "9876543210"})
    assert ok.status_code == 200
    # lower-case order number still works
    assert client.post("/api/orders/track", json={"order_id": num.lower(), "phone": "98765 43210"}).status_code == 200

    for phone in ["", "0", "43210", "1111111111"]:
        r = client.post("/api/orders/track", json={"order_id": num, "phone": phone})
        assert r.status_code == 404, phone


def test_track_by_numeric_id_blocked_for_new_orders(client, db):
    p = make_product(db)
    order = _cod_order(client, p)
    r = client.post("/api/orders/track", json={"order_id": str(order["id"]), "phone": "9876543210"})
    assert r.status_code == 404
    r = client.post("/api/orders/track", json={"order_id": str(order["id"]), "phone": ""})
    assert r.status_code == 404


def test_track_legacy_numeric_id_still_works(client, db):
    p = make_product(db)
    order = _cod_order(client, p)
    o = db.get(models.Order, order["id"])
    o.order_number = None  # simulate an order from before order numbers
    db.commit()
    r = client.post("/api/orders/track", json={"order_id": str(order["id"]), "phone": "9876543210"})
    assert r.status_code == 200


# ---------------- Razorpay ----------------

def _sign(order_id, payment_id):
    return hmac.new(b"rzp_test_secret", f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def fake_razorpay(monkeypatch):
    created = {}

    def create_order(amount_rupees, receipt=""):
        oid = f"order_{len(created) + 1}"
        created[oid] = int(round(amount_rupees * 100))
        return {"id": oid, "amount": created[oid]}

    monkeypatch.setattr(razorpay_client, "create_order", create_order)
    monkeypatch.setattr(razorpay_client, "fetch_order", lambda oid: {"id": oid, "amount": created[oid]})
    return created


def _rzp_create(client, items, **form):
    r = client.post("/api/orders/razorpay/create", json=dict(form, payment_method="razorpay", items=items))
    assert r.status_code == 200, r.text
    return r.json()["razorpay_order_id"]


def _rzp_place(client, items, rzp_order, pay_id, headers=None):
    body = dict(
        CUSTOMER, payment_method="razorpay", items=items,
        razorpay_order_id=rzp_order, razorpay_payment_id=pay_id,
        razorpay_signature=_sign(rzp_order, pay_id),
    )
    return client.post("/api/orders", json=body, headers=headers or {})


def test_razorpay_payment_for_cheap_cart_cannot_buy_expensive_cart(client, db, fake_razorpay):
    cheap = make_product(db, name="Thread", slug="thread", price=50)
    pricey = make_product(db, name="Idol", slug="idol", price=5000)
    rzp_order = _rzp_create(client, [{"product_id": cheap.id, "quantity": 1}])

    r = _rzp_place(client, [{"product_id": pricey.id, "quantity": 1}], rzp_order, "pay_1")
    assert r.status_code == 400
    assert "does not match" in r.json()["detail"]
    assert db.query(models.Order).count() == 0
    db.refresh(pricey)
    assert pricey.stock == 10  # nothing taken


def test_razorpay_happy_path_and_replay_is_idempotent(client, db, fake_razorpay):
    p = make_product(db)
    items = [{"product_id": p.id, "quantity": 2}]
    rzp_order = _rzp_create(client, items)

    r1 = _rzp_place(client, items, rzp_order, "pay_ok")
    assert r1.status_code == 200, r1.text
    assert r1.json()["order"]["status"] == "paid"

    # same signature again (double click / replay) -> same order, no new one
    r2 = _rzp_place(client, items, rzp_order, "pay_ok")
    assert r2.status_code == 200
    assert r2.json()["order"]["id"] == r1.json()["order"]["id"]
    assert db.query(models.Order).count() == 1
    db.refresh(p)
    assert p.stock == 8


def test_razorpay_bad_signature_rejected(client, db, fake_razorpay):
    p = make_product(db)
    items = [{"product_id": p.id, "quantity": 1}]
    rzp_order = _rzp_create(client, items)
    body = dict(CUSTOMER, payment_method="razorpay", items=items, razorpay_order_id=rzp_order,
                razorpay_payment_id="pay_x", razorpay_signature="0" * 64)
    assert client.post("/api/orders", json=body).status_code == 400


def _webhook(client, payment_id, rzp_order, amount, secret=b"whsec_test", event="payment.captured"):
    body = json.dumps({
        "event": event,
        "payload": {"payment": {"entity": {"id": payment_id, "order_id": rzp_order, "amount": amount}}},
    }).encode()
    sig = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return client.post("/api/orders/razorpay/webhook", content=body,
                       headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"})


def test_webhook_rejects_bad_signature(client, db, fake_razorpay):
    r = _webhook(client, "pay_1", "order_1", 100, secret=b"wrong")
    assert r.status_code == 400


def test_webhook_places_order_when_browser_never_returns(client, db, fake_razorpay):
    user = make_user(db)
    p = make_product(db)
    items = [{"product_id": p.id, "quantity": 1}]
    r = client.post("/api/orders/razorpay/create", headers=user_headers(user),
                    json=dict(CUSTOMER, payment_method="razorpay", items=items))
    rzp_order = r.json()["razorpay_order_id"]
    amount = r.json()["amount"]

    # customer paid, closed the tab -> only the webhook arrives
    assert _webhook(client, "pay_lost", rzp_order, amount).status_code == 200
    orders = db.query(models.Order).all()
    assert len(orders) == 1
    assert orders[0].status == "paid"
    assert orders[0].razorpay_payment_id == "pay_lost"
    assert orders[0].user_id == user.id

    # webhook retried by Razorpay -> still one order
    _webhook(client, "pay_lost", rzp_order, amount)
    assert db.query(models.Order).count() == 1

    # browser comes back late -> gets the same order
    late = _rzp_place(client, items, rzp_order, "pay_lost")
    assert late.status_code == 200
    assert late.json()["order"]["id"] == orders[0].id
    assert db.query(models.Order).count() == 1


def test_webhook_amount_mismatch_does_not_place_order(client, db, fake_razorpay):
    p = make_product(db)
    items = [{"product_id": p.id, "quantity": 1}]
    rzp_order = _rzp_create(client, items, **CUSTOMER)
    _webhook(client, "pay_m", rzp_order, 100)
    assert db.query(models.Order).count() == 0


# ---------------- stock + loyalty ----------------

def test_points_only_on_delivery_and_revoked_on_cancel(client, db, admin_headers):
    user = make_user(db)
    p = make_product(db, price=1500)
    order = _cod_order(client, p, headers=user_headers(user))
    db.refresh(user)
    assert user.points == 0  # unpaid COD order earns nothing yet

    url = f"/api/admin/orders/{order['id']}/status"
    assert client.put(url, json={"status": "delivered"}, headers=admin_headers).status_code == 200
    db.refresh(user)
    assert user.points == 1
    # setting delivered again doesn't double count
    client.put(url, json={"status": "shipped"}, headers=admin_headers)
    client.put(url, json={"status": "delivered"}, headers=admin_headers)
    db.refresh(user)
    assert user.points == 1

    client.put(url, json={"status": "cancelled"}, headers=admin_headers)
    db.refresh(user)
    assert user.points == 0


def test_small_orders_earn_no_points(client, db, admin_headers):
    user = make_user(db)
    p = make_product(db, price=200)
    order = _cod_order(client, p, headers=user_headers(user))
    client.put(f"/api/admin/orders/{order['id']}/status", json={"status": "delivered"}, headers=admin_headers)
    db.refresh(user)
    assert user.points == 0


def test_cancel_restores_stock_and_reopen_takes_it_again(client, db, admin_headers):
    p = make_product(db, stock=5)
    order = _cod_order(client, p, qty=3)
    db.refresh(p)
    assert p.stock == 2

    url = f"/api/admin/orders/{order['id']}/status"
    client.put(url, json={"status": "cancelled"}, headers=admin_headers)
    db.refresh(p)
    assert p.stock == 5
    # cancelling twice doesn't add stock twice
    client.put(url, json={"status": "cancelled"}, headers=admin_headers)
    db.refresh(p)
    assert p.stock == 5

    client.put(url, json={"status": "pending"}, headers=admin_headers)
    db.refresh(p)
    assert p.stock == 2


def test_cancel_restores_variant_stock(client, db, admin_headers):
    p = make_product(db, stock=0)
    p.variants.append(models.ProductVariant(size="M", color="Red", stock=4))
    p.variants.append(models.ProductVariant(size="L", color="Red", stock=4))
    p.stock = 8
    db.commit()
    body = dict(CUSTOMER, payment_method="cod",
                items=[{"product_id": p.id, "quantity": 3, "size": "M", "color": "Red"}])
    order = client.post("/api/orders", json=body).json()["order"]
    client.put(f"/api/admin/orders/{order['id']}/status", json={"status": "cancelled"}, headers=admin_headers)
    db.refresh(p)
    stocks = {v.size: v.stock for v in p.variants}
    assert stocks == {"M": 4, "L": 4}
    assert p.stock == 8


def test_gift_redeemed_at_checkout_refunded_on_cancel(client, db, admin_headers):
    user = make_user(db, points=5)
    p = make_product(db, price=300)
    order = _cod_order(client, p, headers=user_headers(user), claim_gift=True)
    assert order["gift_claimed"] is True
    db.refresh(user)
    assert user.points == 0

    client.put(f"/api/admin/orders/{order['id']}/status", json={"status": "cancelled"}, headers=admin_headers)
    db.refresh(user)
    assert user.points == 5


def test_gift_needs_five_points(client, db):
    user = make_user(db, points=4)
    p = make_product(db)
    order = _cod_order(client, p, headers=user_headers(user), claim_gift=True)
    assert order["gift_claimed"] is False
    db.refresh(user)
    assert user.points == 4


def test_cannot_oversell(client, db):
    p = make_product(db, stock=2)
    body = dict(CUSTOMER, payment_method="cod", items=[{"product_id": p.id, "quantity": 3}])
    assert client.post("/api/orders", json=body).status_code == 400
    db.refresh(p)
    assert p.stock == 2


# ---------------- emails ----------------

def test_order_emails_escape_customer_input(client, db):
    p = make_product(db)
    evil = '<a href="http://phish.example">Click</a>'
    order = _cod_order(client, p, customer_name=evil, note=evil, address=evil)
    o = db.get(models.Order, order["id"])
    for html in (_build_html(o), _build_customer_html(o)):
        assert "<a href=\"http://phish.example\">" not in html
        assert "&lt;a href=" in html

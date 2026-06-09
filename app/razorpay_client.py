"""Minimal Razorpay client using stdlib only (no SDK dependency).

create_order(amount_rupees) -> razorpay order dict
verify_signature(order_id, payment_id, signature) -> bool
"""
import base64
import hashlib
import hmac
import json
import urllib.request

from .config import settings

API_BASE = "https://api.razorpay.com/v1"


def is_enabled() -> bool:
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def _auth_header() -> str:
    raw = f"{settings.RAZORPAY_KEY_ID}:{settings.RAZORPAY_KEY_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def create_order(amount_rupees: float, receipt: str = "") -> dict:
    """Create a Razorpay order. Amount is sent in paise."""
    body = json.dumps({
        "amount": int(round(amount_rupees * 100)),  # paise
        "currency": "INR",
        "payment_capture": 1,
        "receipt": receipt or "kirti_order",
    }).encode()
    req = urllib.request.Request(
        f"{API_BASE}/orders",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": _auth_header(),
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """Verify Razorpay payment signature = HMAC_SHA256(order_id|payment_id, secret)."""
    msg = f"{order_id}|{payment_id}".encode()
    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(), msg, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")

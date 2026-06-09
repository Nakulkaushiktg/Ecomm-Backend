import math
import re
from urllib.parse import quote

from .config import settings


def calc_shipping(cfg, subtotal: float, total_weight_g: int) -> float:
    """Shiprocket-style weight slab: per 500g, free above threshold.
    `subtotal` here should be AFTER discount."""
    if subtotal >= cfg.free_shipping_above:
        return 0.0
    slabs = max(1, math.ceil(total_weight_g / 500))
    return float(slabs * cfg.shipping_per_500g)


def calc_cod_fee(cfg, payment_method: str) -> float:
    return float(cfg.cod_fee) if payment_method == "cod" else 0.0


def apply_coupon(db, code: str, subtotal: float):
    """Returns (discount, normalized_code, error). discount=0 if none/invalid."""
    from . import models
    code = (code or "").strip().upper()
    if not code:
        return 0.0, "", ""
    c = db.query(models.Coupon).filter(models.Coupon.code == code).first()
    if not c or not c.is_active:
        return 0.0, "", "Invalid or expired coupon"
    if subtotal < c.min_order:
        return 0.0, "", f"Minimum order Rs.{c.min_order:.0f} for this coupon"
    discount = round(subtotal * c.discount_percent / 100.0, 2)
    return discount, code, ""


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-") or "item"


def build_whatsapp_url(order) -> str:
    """Pre-filled WhatsApp message to the owner with full order details."""
    lines = [
        "*NEW ORDER* \U0001f6cd️",
        f"Order #{order.id}",
        "",
        f"*Customer:* {order.customer_name}",
        f"*Phone:* {order.phone}",
    ]
    if order.email:
        lines.append(f"*Email:* {order.email}")
    lines += [
        "",
        "*Delivery Address:*",
        order.address,
        f"{order.city}, {order.state} - {order.pincode}".strip(", -"),
        "",
        "*Items:*",
    ]
    for it in order.items:
        lines.append(f"- {it.product_name} x{it.quantity} = Rs.{it.price * it.quantity:.0f}")
    lines += [
        "",
        f"Subtotal: Rs.{order.subtotal:.0f}",
    ]
    if order.discount:
        lines.append(f"Discount ({order.coupon_code}): -Rs.{order.discount:.0f}")
    lines += [f"Delivery: Rs.{order.shipping_fee:.0f}"]
    if order.cod_fee:
        lines.append(f"COD Fee: Rs.{order.cod_fee:.0f}")
    lines += [f"*Total: Rs.{order.total:.0f}*"]
    if order.payment_method == "cod":
        lines.append("*Payment:* Cash on Delivery (COD)")
    else:
        lines += [
            "*Payment:* UPI (Paid)",
            f"*UPI Txn Ref:* {order.upi_txn_ref or 'not provided'}",
        ]
    if order.note:
        lines += ["", f"*Note:* {order.note}"]

    text = "\n".join(lines)
    return f"https://wa.me/{settings.OWNER_WHATSAPP}?text={quote(text)}"

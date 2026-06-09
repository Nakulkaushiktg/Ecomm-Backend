import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import optional_user
from ..config import settings
from ..store import get_settings
from ..utils import build_whatsapp_url, calc_shipping, calc_cod_fee, apply_coupon
from ..notify import notify_owner_order, send_contact_email
from .. import models, schemas, razorpay_client

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _match_variant(product, size, color):
    """Find the variant row matching the chosen size+color, if any."""
    for v in product.variants:
        if v.size == size and v.color == color:
            return v
    return None


def _unit_price(product, size, color):
    """Per-unit price: variant price if set (>0), else the product base price."""
    v = _match_variant(product, size, color)
    if v and v.price and v.price > 0:
        return v.price
    return product.price


def _price_cart(db: Session, items):
    """Return (subtotal, total_weight, priced_products) and validate availability."""
    subtotal = 0.0
    weight = 0
    priced = []
    for item in items:
        product = db.query(models.Product).filter(
            models.Product.id == item.product_id
        ).first()
        if not product or not product.is_active:
            raise HTTPException(400, f"Product {item.product_id} unavailable")
        qty = max(1, item.quantity)
        size = getattr(item, "size", "")
        color = getattr(item, "color", "")
        unit = _unit_price(product, size, color)
        subtotal += unit * qty
        weight += (product.weight_grams or 500) * qty
        label = getattr(item, "variant", "") or ", ".join(
            [x for x in [f"Size: {size}" if size else "", f"Color: {color}" if color else ""] if x]
        )
        priced.append((product, qty, label, size, color, unit))
    return round(subtotal, 2), weight, priced


@router.post("/quote", response_model=schemas.ShippingQuote)
def quote(payload: schemas.ShippingQuoteRequest, db: Session = Depends(get_db)):
    if not payload.items:
        raise HTTPException(400, "Cart is empty")
    cfg = get_settings(db)
    subtotal, weight, _ = _price_cart(db, payload.items)
    discount, code, err = apply_coupon(db, payload.coupon_code, subtotal)
    discounted = max(0.0, subtotal - discount)
    method = payload.payment_method if cfg.enable_cod or payload.payment_method != "cod" else "upi"
    shipping = calc_shipping(cfg, discounted, weight)
    cod = calc_cod_fee(cfg, method)
    return schemas.ShippingQuote(
        subtotal=subtotal,
        discount=discount,
        coupon_code=code,
        coupon_error=err,
        shipping_fee=shipping,
        cod_fee=cod,
        total=round(discounted + shipping + cod, 2),
        free_shipping_above=cfg.free_shipping_above,
    )


def _compute_totals(db, cfg, items, coupon_code, method):
    subtotal, weight, priced = _price_cart(db, items)
    discount, code, _ = apply_coupon(db, coupon_code, subtotal)
    discounted = max(0.0, subtotal - discount)
    shipping = calc_shipping(cfg, discounted, weight)
    cod = calc_cod_fee(cfg, method)
    total = round(discounted + shipping + cod, 2)
    return subtotal, discount, code, shipping, cod, total, priced


@router.post("/razorpay/create", response_model=schemas.RazorpayCreateResponse)
def razorpay_create(payload: schemas.ShippingQuoteRequest, db: Session = Depends(get_db)):
    if not razorpay_client.is_enabled():
        raise HTTPException(400, "Razorpay is not configured")
    if not payload.items:
        raise HTTPException(400, "Cart is empty")
    cfg = get_settings(db)
    *_, total, _priced = _compute_totals(db, cfg, payload.items, payload.coupon_code, "razorpay")
    rzp = razorpay_client.create_order(total)
    return schemas.RazorpayCreateResponse(
        razorpay_order_id=rzp["id"],
        razorpay_key_id=settings.RAZORPAY_KEY_ID,
        amount=rzp["amount"],
    )


@router.post("", response_model=schemas.OrderCreateResponse)
def create_order(
    payload: schemas.OrderCreate,
    db: Session = Depends(get_db),
    user=Depends(optional_user),
):
    if not payload.items:
        raise HTTPException(400, "Cart is empty")

    cfg = get_settings(db)
    method = payload.payment_method if payload.payment_method in ("upi", "cod", "razorpay") else "upi"
    if method == "cod" and not cfg.enable_cod:
        raise HTTPException(400, "Cash on Delivery is not available")

    # Razorpay: verify the payment signature before creating the order
    if method == "razorpay":
        if not razorpay_client.is_enabled():
            raise HTTPException(400, "Razorpay is not configured")
        ok = razorpay_client.verify_signature(
            payload.razorpay_order_id,
            payload.razorpay_payment_id,
            payload.razorpay_signature,
        )
        if not ok:
            raise HTTPException(400, "Payment verification failed")

    subtotal, weight, priced = _price_cart(db, payload.items)
    discount, code, _ = apply_coupon(db, payload.coupon_code, subtotal)
    discounted = max(0.0, subtotal - discount)
    shipping = calc_shipping(cfg, discounted, weight)
    cod = calc_cod_fee(cfg, method)

    order = models.Order(
        user_id=user.id if user else None,
        customer_name=payload.customer_name,
        phone=payload.phone,
        email=payload.email,
        address=payload.address,
        city=payload.city,
        state=payload.state,
        pincode=payload.pincode,
        note=payload.note,
        upi_txn_ref=payload.upi_txn_ref if method == "upi" else "",
        razorpay_payment_id=payload.razorpay_payment_id if method == "razorpay" else "",
        payment_method=method,
        subtotal=subtotal,
        discount=discount,
        coupon_code=code,
        shipping_fee=shipping,
        cod_fee=cod,
        total=round(discounted + shipping + cod, 2),
        # razorpay payments are verified, so mark them paid immediately
        status="paid" if method == "razorpay" else "pending",
    )

    low_stock_alerts = []
    for product, qty, label, size, color, unit_price in priced:
        if product.variants:
            # per-variant stock: find the matching size+color row
            match = next(
                (v for v in product.variants if v.size == size and v.color == color),
                None,
            )
            if not match:
                raise HTTPException(400, f"Please select options for '{product.name}'")
            if match.stock < qty:
                raise HTTPException(
                    400, f"'{product.name}' ({label}) has only {match.stock} left"
                )
            match.stock -= qty
            product.stock = sum(v.stock for v in product.variants)
            if 0 <= match.stock < 5:
                low_stock_alerts.append((f"{product.name} ({label})", match.stock))
        else:
            if product.stock < qty:
                raise HTTPException(
                    400, f"'{product.name}' has only {product.stock} left in stock"
                )
            product.stock -= qty  # auto out-of-stock when it hits 0
            if 0 <= product.stock < 5:
                low_stock_alerts.append((product.name, product.stock))
        order.items.append(
            models.OrderItem(
                product_id=product.id,
                product_name=product.name,
                price=unit_price,
                quantity=qty,
                variant=label,
            )
        )

    db.add(order)

    # remember the customer's latest details on their account for next time
    if user:
        user.name = payload.customer_name or user.name
        user.phone = payload.phone or user.phone
        user.address = payload.address or user.address
        user.city = payload.city or user.city
        user.state = payload.state or user.state
        user.pincode = payload.pincode or user.pincode

    db.commit()
    db.refresh(order)

    # automatic notification to owner (if a provider is configured)
    notify_owner_order(order)
    if low_stock_alerts:
        from ..notify import notify_low_stock
        notify_low_stock(low_stock_alerts)

    return schemas.OrderCreateResponse(
        order=order,
        whatsapp_url=build_whatsapp_url(order),
    )


@router.post("/razorpay/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """Server-side confirmation from Razorpay (configure URL in Razorpay dashboard).
    Verifies the webhook signature and marks the matching order paid."""
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if secret:
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(400, "Invalid webhook signature")
    try:
        data = json.loads(body)
        entity = data.get("payload", {}).get("payment", {}).get("entity", {})
        payment_id = entity.get("id", "")
        if payment_id:
            order = db.query(models.Order).filter(
                models.Order.razorpay_payment_id == payment_id
            ).first()
            if order and order.status == "pending":
                order.status = "paid"
                db.commit()
    except Exception as e:
        print("[webhook] parse error:", e)
    return {"ok": True}


@router.get("/track-live")
def track_live(awb: str):
    """Proxy Shiprocket's public tracking so the browser avoids CORS.
    Returns the raw Shiprocket JSON plus a normalized timeline."""
    awb = (awb or "").strip()
    if not awb:
        raise HTTPException(400, "Tracking ID required")
    url = "https://apiv2.shiprocket.in/tracking-form-check?track_type=awb&track_id=" + urllib.parse.quote(awb)
    req = urllib.request.Request(url, headers={
        "accept": "*/*",
        "origin": "https://www.shiprocket.in",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            raw = json.loads(e.read())
        except Exception:
            raw = {"message": "Tracking unavailable", "status_code": e.code}
    except Exception:
        raise HTTPException(502, "Could not reach courier tracking")

    return {"raw": raw, "timeline": _normalize_track(raw), "summary": _track_summary(raw)}


def _dig(obj, *keys):
    """Search nested dict/list for the first matching key."""
    found = []
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in keys:
                    found.append(v)
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(obj)
    return found


def _normalize_track(raw):
    """Best-effort: pull a list of {date,status,location} scan activities."""
    activities = []
    for arr in _dig(raw, "shipment_track_activities", "scans", "activities", "tracking_activities"):
        if isinstance(arr, list):
            for a in arr:
                if not isinstance(a, dict):
                    continue
                activities.append({
                    "date": a.get("date") or a.get("ctime") or a.get("time") or "",
                    "status": a.get("status") or a.get("sr-status-label") or a.get("activity") or a.get("status_detail") or "",
                    "location": a.get("location") or a.get("city") or "",
                })
    return activities


def _track_summary(raw):
    """Best-effort current status string."""
    for v in _dig(raw, "current_status", "shipment_status", "track_status_label", "status"):
        if isinstance(v, str) and v:
            return v
    return ""


@router.post("/track", response_model=schemas.OrderOut)
def track_order(payload: schemas.TrackRequest, db: Session = Depends(get_db)):
    """Public: customer looks up their order by id + phone."""
    order = db.query(models.Order).filter(models.Order.id == payload.order_id).first()
    phone_digits = "".join(filter(str.isdigit, payload.phone))
    if not order or phone_digits[-10:] not in "".join(filter(str.isdigit, order.phone)):
        raise HTTPException(404, "No order found with that ID and phone number")
    return order


@router.post("/contact")
def contact(payload: schemas.ContactMessage):
    if not payload.name.strip() or not payload.message.strip():
        raise HTTPException(400, "Name and message are required")
    try:
        send_contact_email(payload.name, payload.email, payload.phone, payload.message)
    except Exception as e:
        raise HTTPException(502, f"Could not send message: {e}")
    return {"ok": True}


@router.get("/coupons", response_model=list[schemas.CouponOut])
def public_coupons(db: Session = Depends(get_db)):
    """Active coupons shown to customers at checkout."""
    return (
        db.query(models.Coupon)
        .filter(models.Coupon.is_active == True)  # noqa: E712
        .order_by(models.Coupon.discount_percent.desc())
        .all()
    )


@router.get("/config", response_model=schemas.StoreConfig)
def store_config(db: Session = Depends(get_db)):
    cfg = get_settings(db)
    return schemas.StoreConfig(
        upi_id=settings.UPI_ID,
        upi_payee_name=settings.UPI_PAYEE_NAME,
        owner_whatsapp=settings.OWNER_WHATSAPP,
        shipping_per_500g=cfg.shipping_per_500g,
        free_shipping_above=cfg.free_shipping_above,
        cod_fee=cfg.cod_fee,
        enable_cod=cfg.enable_cod,
        enable_razorpay=razorpay_client.is_enabled(),
        razorpay_key_id=settings.RAZORPAY_KEY_ID,
        banner_active=cfg.banner_active,
        banner_text=cfg.banner_text,
    )

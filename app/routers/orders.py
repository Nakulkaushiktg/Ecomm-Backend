import json
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import optional_user
from ..config import settings
from ..store import get_settings
from ..utils import build_whatsapp_url, calc_shipping, calc_cod_fee, apply_coupon
from ..ratelimit import rate_limit
from ..order_ops import compute_totals, place_order, price_cart
from ..notify import (
    notify_low_stock, notify_owner_alert, notify_owner_order, send_contact_email,
    send_customer_email_bg,
)
from .. import models, schemas, razorpay_client

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.post("/quote", response_model=schemas.ShippingQuote)
def quote(payload: schemas.ShippingQuoteRequest, db: Session = Depends(get_db)):
    if not payload.items:
        raise HTTPException(400, "Cart is empty")
    cfg = get_settings(db)
    subtotal, weight, _ = price_cart(db, payload.items)
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


def _paise(rupees: float) -> int:
    return int(round(rupees * 100))


@router.post("/razorpay/create", response_model=schemas.RazorpayCreateResponse)
def razorpay_create(
    payload: schemas.RazorpayCreateRequest,
    db: Session = Depends(get_db),
    user=Depends(optional_user),
):
    if not razorpay_client.is_enabled():
        raise HTTPException(400, "Razorpay is not configured")
    if not payload.items:
        raise HTTPException(400, "Cart is empty")
    cfg = get_settings(db)
    *_, total, _priced = compute_totals(db, cfg, payload.items, payload.coupon_code, "razorpay")
    rzp = razorpay_client.create_order(total)
    # remember the amount (and the checkout form, if sent) for this Razorpay order:
    # the amount is checked when the order is placed, and the form lets the
    # webhook place the order if the customer's browser never comes back
    form = payload.model_dump()
    form["payment_method"] = "razorpay"
    db.add(models.PendingPayment(
        razorpay_order_id=rzp["id"],
        user_id=user.id if user else None,
        payload=form if payload.customer_name.strip() else {},
        amount_paise=int(rzp["amount"]),
    ))
    db.commit()
    return schemas.RazorpayCreateResponse(
        razorpay_order_id=rzp["id"],
        razorpay_key_id=settings.RAZORPAY_KEY_ID,
        amount=rzp["amount"],
    )


def _razorpay_amount(db: Session, razorpay_order_id: str):
    """(amount in paise the customer was charged for, PendingPayment row or None)."""
    pending = (
        db.query(models.PendingPayment)
        .filter(models.PendingPayment.razorpay_order_id == razorpay_order_id)
        .first()
    )
    if pending:
        return pending.amount_paise, pending
    # created before PendingPayment existed — ask Razorpay
    try:
        return int(razorpay_client.fetch_order(razorpay_order_id)["amount"]), None
    except Exception as e:
        print("[razorpay] fetch order failed:", e)
        raise HTTPException(502, "Could not confirm the payment amount. Please contact us.")


def _order_by_payment(db: Session, payment_id: str):
    if not payment_id:
        return None
    return (
        db.query(models.Order)
        .filter(models.Order.razorpay_payment_id == payment_id)
        .first()
    )


def _after_order(order, low_stock_alerts):
    """Notifications after an order is committed — never break the order."""
    notify_owner_order(order)
    try:
        send_customer_email_bg(order)
    except Exception as e:
        print("[order] customer email failed:", e)
    if low_stock_alerts:
        notify_low_stock(low_stock_alerts)


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

    pending = None
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
        # same payment submitted again (retry, or the webhook got there first)
        existing = _order_by_payment(db, payload.razorpay_payment_id)
        if existing:
            return schemas.OrderCreateResponse(order=existing, whatsapp_url=build_whatsapp_url(existing))
        # the signature only proves this payment belongs to that Razorpay order —
        # make sure that Razorpay order was for THIS cart's total
        paid, pending = _razorpay_amount(db, payload.razorpay_order_id)
        *_, total, _p = compute_totals(db, cfg, payload.items, payload.coupon_code, "razorpay")
        if paid != _paise(total):
            raise HTTPException(
                400,
                "Payment amount does not match the order total. Please contact us with "
                f"payment reference {payload.razorpay_payment_id}.",
            )

    order, low_stock_alerts = place_order(db, payload, user, method)
    if pending:
        db.flush()
        pending.order_id = order.id
    try:
        db.commit()
    except IntegrityError:
        # the webhook placed this payment's order at the same moment
        db.rollback()
        existing = _order_by_payment(db, payload.razorpay_payment_id)
        if not existing:
            raise
        return schemas.OrderCreateResponse(order=existing, whatsapp_url=build_whatsapp_url(existing))
    db.refresh(order)

    _after_order(order, low_stock_alerts)
    return schemas.OrderCreateResponse(
        order=order,
        whatsapp_url=build_whatsapp_url(order),
    )


def _place_from_webhook(db: Session, entity: dict):
    """Payment captured but no order yet (browser closed / network died after
    paying): place it from the checkout form saved at /razorpay/create."""
    payment_id = entity.get("id", "")
    rzp_order_id = entity.get("order_id", "")
    pending = (
        db.query(models.PendingPayment)
        .filter(models.PendingPayment.razorpay_order_id == rzp_order_id)
        .first()
    )
    if not pending or pending.order_id or not pending.payload:
        return
    ref = "Razorpay payment %s (order %s), amount Rs.%.0f" % (
        payment_id, rzp_order_id, (entity.get("amount") or 0) / 100
    )
    if int(entity.get("amount") or 0) != pending.amount_paise:
        notify_owner_alert("Payment received but amount mismatch", ref)
        return
    user = None
    if pending.user_id:
        user = db.query(models.User).filter(models.User.id == pending.user_id).first()
    data = dict(pending.payload)
    data.update(
        razorpay_order_id=rzp_order_id, razorpay_payment_id=payment_id, payment_method="razorpay"
    )
    try:
        payload = schemas.OrderCreate(**data)
        order, low_stock_alerts = place_order(db, payload, user, "razorpay")
        db.flush()
        pending.order_id = order.id
        db.commit()
    except IntegrityError:
        db.rollback()  # the browser placed it at the same moment — fine
        return
    except HTTPException as e:
        db.rollback()
        notify_owner_alert(
            "PAID but order could not be created",
            "%s. Reason: %s. Customer: %s %s. Contact the customer or refund from "
            "the Razorpay dashboard." % (ref, e.detail, data.get("customer_name"), data.get("phone")),
        )
        return
    db.refresh(order)
    _after_order(order, low_stock_alerts)


@router.post("/razorpay/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """Server-side confirmation from Razorpay (configure URL in Razorpay dashboard,
    event: payment.captured). Verifies the signature, marks the matching order
    paid, or places the order if the customer's browser never came back."""
    body = await request.body()
    if not settings.RAZORPAY_WEBHOOK_SECRET:
        raise HTTPException(503, "Webhook secret not configured")
    if not razorpay_client.verify_webhook(body, request.headers.get("X-Razorpay-Signature", "")):
        raise HTTPException(400, "Invalid webhook signature")
    try:
        data = json.loads(body)
    except ValueError:
        raise HTTPException(400, "Invalid JSON")
    if data.get("event") not in ("payment.captured", "order.paid"):
        return {"ok": True}
    entity = data.get("payload", {}).get("payment", {}).get("entity", {}) or {}
    payment_id = entity.get("id", "")
    if not payment_id:
        return {"ok": True}
    order = _order_by_payment(db, payment_id)
    if order:
        if order.status == "pending":
            order.status = "paid"
            db.commit()
    else:
        _place_from_webhook(db, entity)
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
def track_order(payload: schemas.TrackRequest, request: Request, db: Session = Depends(get_db)):
    """Public: customer looks up their order by order number (or legacy id) + phone."""
    rate_limit(request, "track", max_calls=20, window_sec=600)
    not_found = HTTPException(404, "No order found with that ID and phone number")
    ref = (payload.order_id or "").strip().upper()
    phone_digits = "".join(filter(str.isdigit, payload.phone))[-10:]
    if len(phone_digits) < 10:
        raise not_found
    order = db.query(models.Order).filter(models.Order.order_number == ref).first()
    if not order and ref.isdigit():
        # legacy orders (placed before order numbers existed) only
        order = (
            db.query(models.Order)
            .filter(models.Order.id == int(ref), models.Order.order_number.is_(None))
            .first()
        )
    if not order:
        raise not_found
    if "".join(filter(str.isdigit, order.phone or ""))[-10:] != phone_digits:
        raise not_found
    return order


@router.post("/contact")
def contact(payload: schemas.ContactMessage, request: Request):
    rate_limit(request, "contact", max_calls=5, window_sec=600)
    if not payload.name.strip() or not payload.message.strip():
        raise HTTPException(400, "Name and message are required")
    try:
        send_contact_email(payload.name, payload.email, payload.phone, payload.message)
    except Exception as e:
        print("[contact] send failed:", e)
        raise HTTPException(502, "Could not send your message right now. Please try WhatsApp instead.")
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


@router.get("/banners", response_model=list[schemas.BannerOut])
def public_banners(db: Session = Depends(get_db)):
    """Active banners; the storefront picks the one whose time window is live."""
    return (
        db.query(models.Banner)
        .filter(models.Banner.is_active == True)  # noqa: E712
        .order_by(models.Banner.created_at.desc())
        .all()
    )


@router.get("/testimonials")
def testimonials(db: Session = Depends(get_db)):
    """Recent 4-5 star reviews (with a comment) for the home page carousel."""
    rows = (
        db.query(models.Review)
        .filter(
            models.Review.rating >= 4,
            models.Review.comment != "",
            models.Review.show_on_site == True,  # noqa: E712
        )
        .order_by(models.Review.created_at.desc())
        .limit(12)
        .all()
    )
    out = []
    for r in rows:
        prod = db.query(models.Product).filter(models.Product.id == r.product_id).first()
        out.append({
            "name": r.name or "Happy Customer",
            "rating": r.rating,
            "comment": r.comment,
            "product_name": prod.name if prod else "",
            "image_url": r.image_url or "",
        })
    return out


@router.post("/subscribe")
def subscribe(payload: schemas.SubscribeRequest, request: Request, db: Session = Depends(get_db)):
    """Store a newsletter subscriber (ignores duplicates) and notify the owner."""
    rate_limit(request, "subscribe", max_calls=5, window_sec=600)
    email = payload.email.strip().lower()
    if "@" not in email or "." not in email:
        raise HTTPException(400, "Please enter a valid email address")
    exists = db.query(models.Subscriber).filter(models.Subscriber.email == email).first()
    if not exists:
        db.add(models.Subscriber(email=email))
        db.commit()
        try:
            from ..notify import _deliver, _email_enabled
            if _email_enabled():
                to = settings.NOTIFY_EMAIL_TO or settings.EMAIL or settings.BREVO_SENDER
                _deliver(to, "New newsletter subscriber", text="New subscriber: %s" % email)
        except Exception as e:
            print("[subscribe] notify failed:", e)
    return {"ok": True}


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
        banner_start=cfg.banner_start or "",
        banner_end=cfg.banner_end or "",
        instagram_url=cfg.instagram_url or "",
        show_stats=cfg.show_stats if cfg.show_stats is not None else True,
        show_loyalty=cfg.show_loyalty if cfg.show_loyalty is not None else True,
        stat_orders=cfg.stat_orders or "500",
        stat_designs=cfg.stat_designs or "50",
    )

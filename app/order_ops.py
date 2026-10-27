"""Order placement + the stock / loyalty side effects of status changes.

Shared by the public orders router (checkout, Razorpay webhook) and the admin
router (status updates), so the rules live in one place:

- stock is taken when an order is placed and put back if it is cancelled
- a loyalty point is earned when an order of Rs.1000+ is DELIVERED (not when
  it is placed, so unpaid / cancelled orders never earn points)
- a redeemed gift costs 5 points at checkout and is refunded on cancel
"""
import secrets

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import models
from .store import get_settings
from .utils import apply_coupon, calc_cod_fee, calc_shipping

LOYALTY_MIN_TOTAL = 1000  # order total that earns a point
GIFT_POINTS = 5           # points needed for a free gift


def gen_order_number(db: Session) -> str:
    """Random, non-guessable public order number, e.g. KTA-7F3K9Q2M."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous 0/O/1/I
    for _ in range(12):
        num = "KTA-" + "".join(secrets.choice(alphabet) for _ in range(8))
        if not db.query(models.Order).filter(models.Order.order_number == num).first():
            return num
    return "KTA-" + secrets.token_hex(5).upper()


def match_variant(product, size, color):
    """Find the variant row matching the chosen size+color, if any."""
    for v in product.variants:
        if v.size == size and v.color == color:
            return v
    return None


def unit_price(product, size, color):
    """Per-unit price: variant price if set (>0), else the product base price."""
    v = match_variant(product, size, color)
    if v and v.price and v.price > 0:
        return v.price
    return product.price


def price_cart(db: Session, items, lock: bool = False):
    """Return (subtotal, total_weight, priced_products) and validate availability.
    lock=True takes row locks on the products (in id order, so two checkouts
    can't deadlock) so concurrent orders can't oversell the same stock."""
    if lock:
        ids = sorted({item.product_id for item in items})
        (
            db.query(models.Product)
            .filter(models.Product.id.in_(ids))
            .order_by(models.Product.id)
            .with_for_update()
            .all()
        )
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
        unit = unit_price(product, size, color)
        subtotal += unit * qty
        weight += (product.weight_grams or 500) * qty
        label = getattr(item, "variant", "") or ", ".join(
            [x for x in [f"Size: {size}" if size else "", f"Color: {color}" if color else ""] if x]
        )
        priced.append((product, qty, label, size, color, unit))
    return round(subtotal, 2), weight, priced


def compute_totals(db: Session, cfg, items, coupon_code, method):
    subtotal, weight, priced = price_cart(db, items)
    discount, code, _ = apply_coupon(db, coupon_code, subtotal)
    discounted = max(0.0, subtotal - discount)
    shipping = calc_shipping(cfg, discounted, weight)
    cod = calc_cod_fee(cfg, method)
    total = round(discounted + shipping + cod, 2)
    return subtotal, discount, code, shipping, cod, total, priced


def place_order(db: Session, payload, user, method: str):
    """Build the order, take stock and redeem a gift if asked. Adds to the
    session but does NOT commit. Returns (order, low_stock_alerts)."""
    cfg = get_settings(db)
    subtotal, weight, priced = price_cart(db, payload.items, lock=True)
    discount, code, _ = apply_coupon(db, payload.coupon_code, subtotal)
    discounted = max(0.0, subtotal - discount)
    shipping = calc_shipping(cfg, discounted, weight)
    cod = calc_cod_fee(cfg, method)

    order = models.Order(
        order_number=gen_order_number(db),
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
        points_awarded=False,
    )

    low_stock_alerts = []
    for product, qty, label, size, color, price in priced:
        if product.variants:
            # per-variant stock: find the matching size+color row
            match = match_variant(product, size, color)
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
                price=price,
                quantity=qty,
                variant=label,
                size=size,
                color=color,
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
        # redeem the gift with this order (ships together). Points for THIS
        # order are only earned once it is delivered — see change_status().
        if payload.claim_gift and (user.points or 0) >= GIFT_POINTS:
            order.gift_claimed = True
            user.points = (user.points or 0) - GIFT_POINTS

    return order, low_stock_alerts


def _locked_product(db: Session, product_id):
    if not product_id:
        return None  # product was deleted
    return (
        db.query(models.Product)
        .filter(models.Product.id == product_id)
        .with_for_update()
        .first()
    )


def restock(db: Session, order):
    """Put a cancelled order's items back into stock."""
    for it in order.items:
        product = _locked_product(db, it.product_id)
        if not product:
            continue
        if product.variants:
            v = match_variant(product, it.size or "", it.color or "")
            if not v:
                # old orders didn't record size/color — can't tell which variant
                print(f"[restock] skip {product.name!r} {it.variant!r}: variant unknown")
                continue
            v.stock += it.quantity
            product.stock = sum(x.stock for x in product.variants)
        else:
            product.stock = (product.stock or 0) + it.quantity


def take_stock(db: Session, order):
    """Take stock again when a cancelled order is re-opened. Raises 400 if an
    item no longer has enough stock (nothing is changed in that case because
    the caller's transaction is rolled back)."""
    for it in order.items:
        product = _locked_product(db, it.product_id)
        if not product:
            continue
        if product.variants:
            v = match_variant(product, it.size or "", it.color or "")
            if not v:
                continue
            if v.stock < it.quantity:
                raise HTTPException(400, f"Not enough stock to re-open: '{product.name}' ({it.variant})")
            v.stock -= it.quantity
            product.stock = sum(x.stock for x in product.variants)
        else:
            if (product.stock or 0) < it.quantity:
                raise HTTPException(400, f"Not enough stock to re-open: '{product.name}'")
            product.stock -= it.quantity


def change_status(db: Session, order, new_status: str):
    """Set an order's status and apply stock + loyalty side effects."""
    prev = order.status
    if new_status == prev:
        return
    user = None
    if order.user_id:
        user = db.query(models.User).filter(models.User.id == order.user_id).first()

    if new_status == "cancelled":
        restock(db, order)
        if user:
            if order.points_awarded:
                user.points = max(0, (user.points or 0) - 1)
                order.points_awarded = False
            if order.gift_claimed:
                user.points = (user.points or 0) + GIFT_POINTS  # refund the gift
    elif prev == "cancelled":
        take_stock(db, order)
        if user and order.gift_claimed:
            if (user.points or 0) >= GIFT_POINTS:
                user.points -= GIFT_POINTS
            else:
                order.gift_claimed = False  # points were spent elsewhere meanwhile

    if (
        new_status == "delivered"
        and user
        and not order.points_awarded
        and (order.total or 0) >= LOYALTY_MIN_TOTAL
    ):
        user.points = (user.points or 0) + 1
        order.points_awarded = True

    order.status = new_status

from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, DateTime, ForeignKey, JSON
)
from sqlalchemy.orm import relationship

from .database import Base

 
class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    slug = Column(String(220), unique=True, index=True, nullable=False)
    description = Column(Text, default="")
    # category: god | jewellery | clothes | woolen
    category = Column(String(50), index=True, nullable=False)
    material = Column(String(120), default="")  # e.g. "Handmade wool", "Cotton"
    price = Column(Float, nullable=False)
    mrp = Column(Float, default=0)  # original price for strike-through
    stock = Column(Integer, default=0)
    weight_grams = Column(Integer, default=500)  # for shipping calc
    images = Column(JSON, default=list)  # list of image URLs
    videos = Column(JSON, default=list)  # list of video URLs
    sizes = Column(JSON, default=list)   # optional list e.g. ["S","M","L"]
    colors = Column(JSON, default=list)  # optional list e.g. ["Maroon","Cream"]
    is_active = Column(Boolean, default=True)
    is_featured = Column(Boolean, default=False)
    is_bestseller = Column(Boolean, default=False)
    is_new = Column(Boolean, default=False)
    is_trending = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    variants = relationship(
        "ProductVariant", back_populates="product",
        cascade="all, delete-orphan", lazy="selectin",
    )


class ProductVariant(Base):
    __tablename__ = "product_variants"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False)
    size = Column(String(40), default="")
    color = Column(String(60), default="")
    stock = Column(Integer, default=0)
    price = Column(Float, default=0)  # 0 = use the product's base price
    mrp = Column(Float, default=0)    # 0 = use the product's base mrp

    product = relationship("Product", back_populates="variants")


class User(Base):
    """Customer account (storefront login)."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    email = Column(String(150), unique=True, index=True, nullable=False)
    phone = Column(String(20), default="")
    password_hash = Column(String(200), nullable=False)
    address = Column(Text, default="")
    city = Column(String(100), default="")
    state = Column(String(100), default="")
    pincode = Column(String(15), default="")
    reset_requested = Column(Boolean, default=False)  # customer asked admin to reset
    reset_otp = Column(String(200), default="")        # hashed one-time reset code
    reset_otp_expiry = Column(DateTime, nullable=True)  # code valid until this time
    created_at = Column(DateTime, default=datetime.utcnow)


class WishlistItem(Base):
    """A product saved to a customer's wishlist."""
    __tablename__ = "wishlist_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String(20), unique=True, index=True)  # public, non-guessable
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    customer_name = Column(String(150), nullable=False)
    phone = Column(String(20), nullable=False)
    email = Column(String(150), default="")
    address = Column(Text, nullable=False)
    city = Column(String(100), default="")
    state = Column(String(100), default="")
    pincode = Column(String(15), default="")
    note = Column(Text, default="")

    subtotal = Column(Float, default=0)
    discount = Column(Float, default=0)
    coupon_code = Column(String(40), default="")
    shipping_fee = Column(Float, default=0)
    cod_fee = Column(Float, default=0)
    total = Column(Float, nullable=False)
    payment_method = Column(String(30), default="upi")  # upi | cod | razorpay
    upi_txn_ref = Column(String(120), default="")
    razorpay_payment_id = Column(String(120), default="")
    # status: pending | paid | shipped | delivered | cancelled
    status = Column(String(30), default="pending", index=True)
    # shiprocket / courier shipment tracking
    courier = Column(String(80), default="")
    tracking_id = Column(String(120), default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    items = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(60), unique=True, index=True, nullable=False)  # slug used by products
    label = Column(String(120), nullable=False)
    emoji = Column(String(16), default="🧶")
    image = Column(String(500), default="")
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)


class Setting(Base):
    """Single-row store settings, editable from admin panel."""
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True)
    enable_cod = Column(Boolean, default=True)
    shipping_per_500g = Column(Float, default=50)
    free_shipping_above = Column(Float, default=1500)
    cod_fee = Column(Float, default=40)
    # admin login (editable from panel); seeded from .env on first run
    admin_username = Column(String(80), default="")
    admin_password_hash = Column(String(200), default="")
    # hero sale/offer banner (editable from admin)
    banner_active = Column(Boolean, default=False)
    banner_text = Column(String(240), default="")
    # optional schedule (stored as "YYYY-MM-DDTHH:MM" wall-clock strings; blank = none)
    banner_start = Column(String(40), default="")
    banner_end = Column(String(40), default="")
    # Instagram profile URL — if set, storefront shows a "Follow us" section
    instagram_url = Column(String(200), default="")


class Coupon(Base):
    __tablename__ = "coupons"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(40), unique=True, index=True, nullable=False)
    discount_percent = Column(Float, default=0)   # e.g. 10 = 10% off
    min_order = Column(Float, default=0)           # minimum subtotal to apply
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    name = Column(String(120), nullable=False)
    rating = Column(Integer, default=5)  # 1..5
    comment = Column(Text, default="")
    image_url = Column(String(300), default="")  # optional review photo
    show_on_site = Column(Boolean, default=True)  # admin can hide a review from the store
    created_at = Column(DateTime, default=datetime.utcnow)


class Banner(Base):
    """A scheduled announcement banner. Multiple can exist; the storefront shows
    the one whose time window is currently active."""
    __tablename__ = "banners"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(String(240), nullable=False)
    start_at = Column(String(40), default="")  # "YYYY-MM-DDTHH:MM" wall-clock, blank = always
    end_at = Column(String(40), default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Subscriber(Base):
    """Newsletter email subscriber."""
    __tablename__ = "subscribers"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(150), unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"))
    product_name = Column(String(200), nullable=False)
    price = Column(Float, nullable=False)
    quantity = Column(Integer, default=1)
    variant = Column(String(120), default="")  # e.g. "Size: M, Color: Maroon"

    order = relationship("Order", back_populates="items")
    product = relationship("Product")

    @property
    def product_slug(self) -> str:
        return self.product.slug if self.product else ""

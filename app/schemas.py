from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


# ---------- Product variants ----------
class VariantIn(BaseModel):
    size: str = ""
    color: str = ""
    stock: int = 0


class VariantOut(VariantIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---------- Product ----------
class ProductBase(BaseModel):
    name: str
    description: str = ""
    category: str
    material: str = ""
    price: float
    mrp: float = 0
    stock: int = 0
    weight_grams: int = 500
    images: List[str] = []
    videos: List[str] = []
    sizes: List[str] = []
    colors: List[str] = []
    is_active: bool = True
    is_featured: bool = False
    is_bestseller: bool = False


class ProductCreate(ProductBase):
    variants: List[VariantIn] = []


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    material: Optional[str] = None
    price: Optional[float] = None
    mrp: Optional[float] = None
    stock: Optional[int] = None
    weight_grams: Optional[int] = None
    images: Optional[List[str]] = None
    videos: Optional[List[str]] = None
    sizes: Optional[List[str]] = None
    colors: Optional[List[str]] = None
    is_active: Optional[bool] = None
    is_featured: Optional[bool] = None
    is_bestseller: Optional[bool] = None
    variants: Optional[List[VariantIn]] = None


class ProductOut(ProductBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    slug: str
    created_at: datetime
    avg_rating: float = 0
    review_count: int = 0
    variants: List[VariantOut] = []


# ---------- Order ----------
class OrderItemIn(BaseModel):
    product_id: int
    quantity: int = 1
    variant: str = ""
    size: str = ""
    color: str = ""


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: Optional[int]
    product_name: str
    price: float
    quantity: int
    variant: str = ""


class OrderCreate(BaseModel):
    customer_name: str
    phone: str
    email: str = ""
    address: str
    city: str = ""
    state: str = ""
    pincode: str = ""
    note: str = ""
    payment_method: str = "upi"  # upi | cod | razorpay
    upi_txn_ref: str = ""
    coupon_code: str = ""
    # razorpay (sent only after successful gateway payment)
    razorpay_order_id: str = ""
    razorpay_payment_id: str = ""
    razorpay_signature: str = ""
    items: List[OrderItemIn]


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_name: str
    phone: str
    email: str
    address: str
    city: str
    state: str
    pincode: str
    note: str
    subtotal: float
    discount: float
    coupon_code: str
    shipping_fee: float
    cod_fee: float
    total: float
    payment_method: str
    upi_txn_ref: str
    razorpay_payment_id: str
    status: str
    courier: str
    tracking_id: str
    created_at: datetime
    items: List[OrderItemOut]


class OrderCreateResponse(BaseModel):
    order: OrderOut
    whatsapp_url: str


class OrderStatusUpdate(BaseModel):
    status: str


class ShipmentUpdate(BaseModel):
    courier: str = ""
    tracking_id: str = ""


class ShippingQuoteRequest(BaseModel):
    payment_method: str = "upi"
    coupon_code: str = ""
    items: List[OrderItemIn]


class ShippingQuote(BaseModel):
    subtotal: float
    discount: float
    coupon_code: str
    coupon_error: str
    shipping_fee: float
    cod_fee: float
    total: float
    free_shipping_above: float


# ---------- Categories ----------
class CategoryBase(BaseModel):
    label: str
    emoji: str = "🧶"
    image: str = ""
    is_active: bool = True
    sort_order: int = 0


class CategoryCreate(CategoryBase):
    key: str = ""  # optional; auto-slugged from label if blank


class CategoryUpdate(BaseModel):
    label: Optional[str] = None
    emoji: Optional[str] = None
    image: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class CategoryOut(CategoryBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    key: str


# ---------- Coupons ----------
class CouponBase(BaseModel):
    code: str
    discount_percent: float
    min_order: float = 0
    is_active: bool = True


class CouponCreate(CouponBase):
    pass


class CouponOut(CouponBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---------- Reviews ----------
class ReviewCreate(BaseModel):
    name: str
    rating: int = 5
    comment: str = ""
    image_url: str = ""


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    rating: int
    comment: str
    image_url: str = ""
    created_at: datetime


# ---------- Settings ----------
class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    enable_cod: bool
    shipping_per_500g: float
    free_shipping_above: float
    cod_fee: float
    admin_username: str = ""
    banner_active: bool = False
    banner_text: str = ""


class SettingsUpdate(BaseModel):
    enable_cod: Optional[bool] = None
    shipping_per_500g: Optional[float] = None
    free_shipping_above: Optional[float] = None
    cod_fee: Optional[float] = None
    banner_active: Optional[bool] = None
    banner_text: Optional[str] = None


# ---------- Track order (public) ----------
class TrackRequest(BaseModel):
    order_id: int
    phone: str


class ContactMessage(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    message: str


# ---------- Auth ----------
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CredentialsUpdate(BaseModel):
    current_username: str
    current_password: str
    new_username: str = ""
    new_password: str = ""


# ---------- Config (public store info) ----------
class StoreConfig(BaseModel):
    upi_id: str
    upi_payee_name: str
    owner_whatsapp: str
    shipping_per_500g: float
    free_shipping_above: float
    cod_fee: float
    enable_cod: bool
    enable_razorpay: bool
    razorpay_key_id: str
    banner_active: bool = False
    banner_text: str = ""


class RazorpayCreateResponse(BaseModel):
    razorpay_order_id: str
    razorpay_key_id: str
    amount: int          # paise
    currency: str = "INR"

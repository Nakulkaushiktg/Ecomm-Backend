from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/ecomm"
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123"
    SECRET_KEY: str = "dev_secret_change_me"
    OWNER_WHATSAPP: str = "919876543210"
    UPI_ID: str = "yourname@upi"
    UPI_PAYEE_NAME: str = "Your Store"
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    # ---- Order notification to owner ----
    NOTIFY_PROVIDER: str = "none"      # none | email | greenapi | callmebot
    # email (Gmail SMTP — used for local dev; Render free blocks outbound SMTP)
    EMAIL: str = ""                    # your gmail address (sender + receiver)
    EMAIL_PASSWORD: str = ""           # gmail App Password
    NOTIFY_EMAIL_TO: str = ""          # where to receive (blank = same as EMAIL)
    # Resend HTTP API (works on Render where SMTP is blocked). If set, used over SMTP.
    RESEND_API_KEY: str = ""
    RESEND_FROM: str = "Kirti Thread Art <onboarding@resend.dev>"
    # whatsapp providers
    CALLMEBOT_APIKEY: str = ""
    GREENAPI_INSTANCE: str = ""
    GREENAPI_TOKEN: str = ""

    # ---- Cloudinary image/video storage (persists across Render restarts) ----
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""

    # ---- Razorpay payment gateway (optional, auto-verified payments) ----
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""

    # ---- Shipping (Shiprocket-style, weight based) ----
    SHIPPING_PER_500G: float = 50      # charge per 500g slab
    FREE_SHIPPING_ABOVE: float = 1500  # free delivery if subtotal >= this
    COD_FEE: float = 40                # extra fee for Cash on Delivery orders
    ENABLE_COD: bool = True

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days


settings = Settings()

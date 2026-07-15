"""Automatic order notification to the store owner.

Set NOTIFY_PROVIDER in .env to ONE of:

  email       -> needs EMAIL + EMAIL_PASSWORD (Gmail App Password)
  greenapi    -> needs GREENAPI_INSTANCE + GREENAPI_TOKEN
  callmebot   -> needs CALLMEBOT_APIKEY
  none        -> disabled (default)

Sending runs in a background thread so it never slows/breaks the order.
"""
import json
import smtplib
import threading
import urllib.error
import urllib.parse
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import settings


def _email_enabled() -> bool:
    """True if Brevo, Resend or Gmail SMTP credentials are configured."""
    return (
        bool(settings.BREVO_API_KEY and settings.BREVO_SENDER)
        or bool(settings.RESEND_API_KEY)
        or bool(settings.EMAIL and settings.EMAIL_PASSWORD)
    )


def _send_via_brevo(to_addr, subject, html="", text="", reply_to=""):
    payload = {
        "sender": {"name": settings.BREVO_SENDER_NAME, "email": settings.BREVO_SENDER},
        "to": [{"email": to_addr}],
        "subject": subject,
    }
    if html:
        payload["htmlContent"] = html
    if text:
        payload["textContent"] = text
    if reply_to:
        payload["replyTo"] = {"email": reply_to}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=data,
        headers={
            "api-key": settings.BREVO_API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        raise RuntimeError(
            "Brevo %s sending to %s as '%s': %s"
            % (e.code, to_addr, settings.BREVO_SENDER, body)
        )


def _send_via_resend(to_addr, subject, html="", text="", reply_to=""):
    payload = {
        "from": settings.RESEND_FROM,
        "to": [to_addr],
        "subject": subject,
    }
    if html:
        payload["html"] = html
    if text:
        payload["text"] = text
    if reply_to:
        payload["reply_to"] = reply_to
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=data,
        headers={
            "Authorization": "Bearer %s" % settings.RESEND_API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Cloudflare in front of api.resend.com blocks the default
            # "Python-urllib" agent (error 1010); use a browser-like UA.
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        # surface Resend's real message (which 'to'/account is allowed) + sender used
        raise RuntimeError(
            "Resend %s sending to %s as '%s': %s"
            % (e.code, to_addr, settings.RESEND_FROM, body)
        )


def _send_via_smtp(to_addr, subject, html="", text="", reply_to=""):
    sender = settings.EMAIL
    password = (settings.EMAIL_PASSWORD or "").replace(" ", "")  # Gmail app password
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = "Kirti Thread Art <%s>" % sender
    msg["To"] = to_addr
    if reply_to:
        msg["Reply-To"] = reply_to
    if text:
        msg.attach(MIMEText(text, "plain"))
    if html:
        msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
        s.starttls()
        s.login(sender, password)
        s.send_message(msg)


def _deliver(to_addr, subject, html="", text="", reply_to=""):
    """Send one email. Prefers Brevo, then Resend (both Render-safe HTTP APIs),
    finally Gmail SMTP (local dev only)."""
    if settings.BREVO_API_KEY and settings.BREVO_SENDER:
        _send_via_brevo(to_addr, subject, html, text, reply_to)
    elif settings.RESEND_API_KEY:
        _send_via_resend(to_addr, subject, html, text, reply_to)
    else:
        _send_via_smtp(to_addr, subject, html, text, reply_to)


def _build_html(order) -> str:
    rows = "".join(
        """
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;">{name}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:center;">{qty}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;">&#8377;{amt:.0f}</td>
        </tr>""".format(name=it.product_name, qty=it.quantity, amt=it.price * it.quantity)
        for it in order.items
    )

    pay = (
        "Cash on Delivery"
        if order.payment_method == "cod"
        else "UPI &nbsp;&middot;&nbsp; Ref: %s" % (order.upi_txn_ref or "not provided")
    )
    discount_row = (
        '<tr><td style="padding:4px 12px;color:#15803d;">Discount (%s)</td>'
        '<td style="padding:4px 12px;text-align:right;color:#15803d;">&minus; &#8377;%.0f</td></tr>'
        % (order.coupon_code, order.discount)
        if order.discount else ""
    )
    cod_row = (
        '<tr><td style="padding:4px 12px;color:#555;">COD Fee</td>'
        '<td style="padding:4px 12px;text-align:right;">&#8377;%.0f</td></tr>' % order.cod_fee
        if order.cod_fee else ""
    )
    addr = "%s<br>%s" % (
        order.address,
        ", ".join([x for x in [order.city, order.state, order.pincode] if x]),
    )

    return """
<div style="background:#f5f1e8;padding:24px 0;font-family:Arial,Helvetica,sans-serif;color:#2b231e;">
  <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(0,0,0,.08);">
    <div style="background:#7B2D26;padding:22px 28px;color:#fff;">
      <div style="font-size:13px;letter-spacing:2px;color:#E8C77E;">Kirti Thread Art</div>
      <div style="font-size:22px;font-weight:bold;margin-top:4px;">New Order Received &#127881;</div>
    </div>

    <div style="padding:24px 28px;">
      <table style="width:100%;font-size:14px;">
        <tr>
          <td style="color:#888;">Order</td>
          <td style="text-align:right;font-weight:bold;color:#7B2D26;">#{oid}</td>
        </tr>
      </table>

      <div style="margin:18px 0;padding:14px 16px;background:#faf6ee;border-radius:10px;font-size:14px;">
        <div style="font-weight:bold;margin-bottom:6px;">&#128100; Customer</div>
        {cname}<br>
        &#128222; {phone}<br><br>
        <div style="font-weight:bold;margin-bottom:6px;">&#128230; Delivery Address</div>
        {addr}
      </div>

      <table style="width:100%;border-collapse:collapse;font-size:14px;margin-top:8px;">
        <thead>
          <tr style="background:#f3e9d7;text-align:left;">
            <th style="padding:10px 12px;">Item</th>
            <th style="padding:10px 12px;text-align:center;">Qty</th>
            <th style="padding:10px 12px;text-align:right;">Amount</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>

      <table style="width:100%;font-size:14px;margin-top:12px;">
        <tr><td style="padding:4px 12px;color:#555;">Subtotal</td><td style="padding:4px 12px;text-align:right;">&#8377;{subtotal:.0f}</td></tr>
        {discount_row}
        <tr><td style="padding:4px 12px;color:#555;">Delivery</td><td style="padding:4px 12px;text-align:right;">&#8377;{shipping:.0f}</td></tr>
        {cod_row}
        <tr><td style="padding:10px 12px;font-weight:bold;font-size:16px;border-top:2px solid #f0e6d2;">Total</td>
            <td style="padding:10px 12px;text-align:right;font-weight:bold;font-size:16px;color:#7B2D26;border-top:2px solid #f0e6d2;">&#8377;{total:.0f}</td></tr>
      </table>

      <div style="margin-top:16px;padding:12px 16px;background:#7B2D26;border-radius:10px;color:#fff;font-size:14px;">
        &#128179; Payment: <b>{pay}</b>
      </div>

      <p style="margin-top:20px;font-size:13px;color:#888;text-align:center;">
        Log in to your admin panel to confirm and process this order.
      </p>
    </div>
    <div style="background:#f3e9d7;padding:14px;text-align:center;font-size:12px;color:#7B2D26;">
      Kirti Thread Art &middot; Woolen &middot; Sacred &middot; Crafted
    </div>
  </div>
</div>""".format(
        oid=order.id, cname=order.customer_name, phone=order.phone, addr=addr,
        rows=rows, subtotal=order.subtotal, discount_row=discount_row,
        shipping=order.shipping_fee, cod_row=cod_row, total=order.total, pay=pay,
    )


def _build_text(order) -> str:
    lines = [
        "🛍️ NEW ORDER #%s" % order.id,
        "",
        "Customer: %s" % order.customer_name,
        "Phone: %s" % order.phone,
        "",
        "Address:",
        order.address,
        ", ".join([x for x in [order.city, order.state, order.pincode] if x]),
        "",
        "Items:",
    ]
    for it in order.items:
        lines.append("- %s x%d = Rs.%.0f" % (it.product_name, it.quantity, it.price * it.quantity))
    lines.append("")
    lines.append("Subtotal: Rs.%.0f" % order.subtotal)
    if order.discount:
        lines.append("Discount (%s): -Rs.%.0f" % (order.coupon_code, order.discount))
    lines.append("Delivery: Rs.%.0f" % order.shipping_fee)
    if order.cod_fee:
        lines.append("COD Fee: Rs.%.0f" % order.cod_fee)
    lines.append("TOTAL: Rs.%.0f" % order.total)
    if order.payment_method == "cod":
        lines.append("Payment: Cash on Delivery")
    else:
        lines.append("Payment: UPI | Ref: %s" % (order.upi_txn_ref or "-"))
    return "\n".join(lines)


def _send_callmebot(text: str):
    phone = settings.OWNER_WHATSAPP
    url = (
        "https://api.callmebot.com/whatsapp.php?phone=%s&text=%s&apikey=%s"
        % (phone, urllib.parse.quote(text), settings.CALLMEBOT_APIKEY)
    )
    urllib.request.urlopen(url, timeout=15).read()


def _send_greenapi(text: str):
    inst = settings.GREENAPI_INSTANCE
    token = settings.GREENAPI_TOKEN
    chat_id = "%s@c.us" % settings.OWNER_WHATSAPP
    url = "https://api.green-api.com/waInstance%s/sendMessage/%s" % (inst, token)
    data = json.dumps({"chatId": chat_id, "message": text}).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=15).read()


def _build_customer_html(order) -> str:
    """Warm, branded confirmation email sent TO the customer."""
    rows = "".join(
        """
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;">{name}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:center;">{qty}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;">&#8377;{amt:.0f}</td>
        </tr>""".format(name=it.product_name, qty=it.quantity, amt=it.price * it.quantity)
        for it in order.items
    )
    status_msg = {
        "pending": "Your order is confirmed and is being lovingly prepared. 🙏",
        "paid": "Your payment is confirmed. We're now preparing your order with care. ✅",
        "shipped": "Great news — your order is on its way! 🚚",
        "delivered": "Your order has been delivered. We hope you love it! 📦",
        "cancelled": "Your order has been cancelled. Please reach out if this was a mistake.",
    }.get(order.status, "Your order is confirmed. 🙏")

    track_block = ""
    if order.tracking_id:
        track_block = """
      <div style="margin-top:16px;padding:14px 16px;background:#faf6ee;border-radius:10px;font-size:14px;">
        <b>&#128205; Shipment Details</b><br>
        Courier: {courier}<br>
        Tracking ID: {tid}<br>
        <a href="https://www.shiprocket.in/shipment-tracking/{tid}" style="color:#7B2D26;">Track your shipment &#8599;</a>
      </div>""".format(courier=order.courier or "Courier", tid=order.tracking_id)

    addr = "%s<br>%s" % (
        order.address,
        ", ".join([x for x in [order.city, order.state, order.pincode] if x]),
    )
    pay = (
        "Cash on Delivery"
        if order.payment_method == "cod"
        else ("Paid Online" if order.payment_method == "razorpay" else "UPI")
    )
    tot_rows = '<tr><td style="padding:3px 12px;color:#666;">Subtotal</td><td style="padding:3px 12px;text-align:right;">&#8377;%.0f</td></tr>' % order.subtotal
    if order.discount:
        tot_rows += '<tr><td style="padding:3px 12px;color:#15803d;">Discount (%s)</td><td style="padding:3px 12px;text-align:right;color:#15803d;">&minus; &#8377;%.0f</td></tr>' % (order.coupon_code, order.discount)
    tot_rows += '<tr><td style="padding:3px 12px;color:#666;">Delivery</td><td style="padding:3px 12px;text-align:right;">&#8377;%.0f</td></tr>' % order.shipping_fee
    if order.cod_fee:
        tot_rows += '<tr><td style="padding:3px 12px;color:#666;">COD Fee</td><td style="padding:3px 12px;text-align:right;">&#8377;%.0f</td></tr>' % order.cod_fee
    tot_rows += '<tr><td style="padding:8px 12px;font-weight:bold;font-size:16px;border-top:2px solid #f0e6d2;">Total</td><td style="padding:8px 12px;text-align:right;font-weight:bold;font-size:16px;color:#7B2D26;border-top:2px solid #f0e6d2;">&#8377;%.0f</td></tr>' % order.total
    tot_rows += '<tr><td style="padding:3px 12px;color:#666;">Payment</td><td style="padding:3px 12px;text-align:right;">%s</td></tr>' % pay
    return """
<div style="background:#f5f1e8;padding:24px 0;font-family:Arial,Helvetica,sans-serif;color:#2b231e;">
  <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(0,0,0,.08);">
    <div style="background:#7B2D26;padding:26px 28px;color:#fff;text-align:center;">
      <div style="font-size:13px;letter-spacing:3px;color:#E8C77E;">Kirti Thread Art</div>
      <div style="font-size:24px;font-weight:bold;margin-top:6px;">Thank You, {cname}! &#128153;</div>
    </div>
    <div style="padding:24px 28px;">
      <p style="font-size:15px;line-height:1.6;">
        {status_msg}<br>
        Here is a summary of your order <b>#{oid}</b>.
      </p>

      <table style="width:100%;border-collapse:collapse;font-size:14px;margin-top:10px;">
        <thead>
          <tr style="background:#f3e9d7;text-align:left;">
            <th style="padding:10px 12px;">Item</th>
            <th style="padding:10px 12px;text-align:center;">Qty</th>
            <th style="padding:10px 12px;text-align:right;">Amount</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>

      <table style="width:100%;font-size:14px;margin-top:12px;">
        {tot_rows}
      </table>

      <div style="margin-top:12px;padding:12px 16px;background:#faf6ee;border-radius:10px;font-size:14px;">
        <b>&#128230; Delivery Address</b><br>{addr}
      </div>
      {track_block}

      <p style="margin-top:20px;font-size:14px;line-height:1.6;color:#555;">
        If you have any questions, just reply to this email — we're always happy to help.
      </p>
      <p style="font-size:14px;">With gratitude,<br><b>Team Kirti Thread Art</b> &#129528;&#128367;</p>
    </div>
    <div style="background:#f3e9d7;padding:14px;text-align:center;font-size:12px;color:#7B2D26;">
      Kirti Thread Art &middot; Woolen &middot; Sacred &middot; Crafted
    </div>
  </div>
</div>""".format(
        cname=order.customer_name, status_msg=status_msg, oid=order.id,
        rows=rows, tot_rows=tot_rows, addr=addr, track_block=track_block,
    )


def send_contact_email(name: str, email: str, phone: str, message: str) -> None:
    """Send a Contact Us enquiry to the store owner. Raises on failure."""
    if not _email_enabled():
        raise ValueError("Store email is not configured")
    to_addr = settings.NOTIFY_EMAIL_TO or settings.EMAIL
    safe_msg = (message or "").replace("\n", "<br>")
    html = """
<div style="background:#f5f1e8;padding:24px 0;font-family:Arial,Helvetica,sans-serif;color:#2b231e;">
  <div style="max-width:540px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(0,0,0,.08);">
    <div style="background:#7B2D26;padding:20px 26px;color:#fff;">
      <div style="font-size:12px;letter-spacing:2px;color:#E8C77E;">Kirti Thread Art</div>
      <div style="font-size:20px;font-weight:bold;margin-top:4px;">New Contact Enquiry &#9993;</div>
    </div>
    <div style="padding:22px 26px;font-size:14px;line-height:1.6;">
      <p><b>Name:</b> {name}<br>
         <b>Email:</b> {email}<br>
         <b>Phone:</b> {phone}</p>
      <div style="margin-top:10px;padding:14px 16px;background:#faf6ee;border-radius:10px;">
        {message}
      </div>
    </div>
  </div>
</div>""".format(name=name, email=email or "-", phone=phone or "-", message=safe_msg)

    text = "From %s (%s, %s):\n\n%s" % (name, email, phone, message)
    _deliver(to_addr, "Contact Enquiry from %s" % name, html=html, text=text, reply_to=email or "")


def send_reset_otp(to_email: str, otp: str, name: str = "") -> None:
    """Email a password-reset one-time code to the customer. Raises on failure."""
    if not _email_enabled():
        raise ValueError("Store email is not configured")
    hi = ("Hi %s," % name) if name else "Hi,"
    html = """
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:24px;
      border:1px solid #eee;border-radius:12px;">
      <h2 style="color:#7B2D26;margin:0 0 8px;">Password Reset</h2>
      <p style="color:#444;">%s</p>
      <p style="color:#444;">Use this code to reset your Kirti Thread Art password:</p>
      <p style="font-size:32px;font-weight:bold;letter-spacing:6px;color:#7B2D26;
        text-align:center;margin:18px 0;">%s</p>
      <p style="color:#888;font-size:13px;">This code expires in 10 minutes. If you didn't
      request this, you can safely ignore this email.</p>
    </div>""" % (hi, otp)
    text = "Your Kirti Thread Art password reset code is %s. It expires in 10 minutes." % otp
    _deliver(to_email, "Your password reset code: %s" % otp, html=html, text=text)


def send_customer_email(order) -> None:
    """Send a branded confirmation email to the customer. Raises on failure."""
    if not order.email:
        raise ValueError("Customer has no email address")
    if not _email_enabled():
        raise ValueError("Store email is not configured")
    plain = "Thank you for your order #%s with Kirti Thread Art. Total: Rs.%.0f." % (
        order.id, order.total
    )
    _deliver(
        order.email,
        "Your Kirti Thread Art Order #%s" % order.id,
        html=_build_customer_html(order),
        text=plain,
    )


def _send_email(text: str, order):
    to_addr = settings.NOTIFY_EMAIL_TO or settings.EMAIL
    subject = "New Order #%s - Rs.%.0f (%s)" % (
        order.id, order.total, order.customer_name
    )
    _deliver(to_addr, subject, html=_build_html(order), text=text)


def _worker(text: str, order):
    provider = (settings.NOTIFY_PROVIDER or "none").lower()
    try:
        if provider == "email" and _email_enabled():
            _send_email(text, order)
        elif provider == "callmebot" and settings.CALLMEBOT_APIKEY:
            _send_callmebot(text)
        elif provider == "greenapi" and settings.GREENAPI_INSTANCE and settings.GREENAPI_TOKEN:
            _send_greenapi(text)
    except Exception as e:  # never break the order on notify failure
        print("[notify] send failed:", e)


def notify_password_reset(user):
    """Tell the store owner a customer wants their password reset."""
    if not _email_enabled():
        return
    to_addr = settings.NOTIFY_EMAIL_TO or settings.EMAIL
    html = (
        '<div style="font-family:Arial,sans-serif;color:#2b231e;">'
        '<h2 style="color:#7B2D26;">Password reset requested</h2>'
        f'<p><b>{user.name}</b> ({user.email}'
        + (f', {user.phone}' if user.phone else '')
        + ') forgot their password and asked for help.</p>'
        '<p>Open the admin panel &rarr; Customers &rarr; Reset Password, '
        'then share the new password with them.</p></div>'
    )
    text = (
        f"{user.name} ({user.email}) requested a password reset. "
        "Reset it from Admin > Customers and share the new password."
    )
    try:
        _deliver(to_addr, "Password reset request — Kirti Thread Art", html=html, text=text)
    except Exception as e:
        print("[notify] reset request email failed:", e)


def notify_owner_order(order):
    """Fire-and-forget notification to the owner about a new order."""
    if (settings.NOTIFY_PROVIDER or "none").lower() == "none":
        return
    text = _build_text(order)
    threading.Thread(target=_worker, args=(text, order), daemon=True).start()


def _low_stock_worker(items):
    lines = ["⚠️ LOW STOCK ALERT", ""]
    for name, stock in items:
        lines.append("- %s: only %d left" % (name, stock))
    lines.append("")
    lines.append("Restock soon on your admin panel.")
    text = "\n".join(lines)
    provider = (settings.NOTIFY_PROVIDER or "none").lower()
    try:
        if provider == "email" and _email_enabled():
            to_addr = settings.NOTIFY_EMAIL_TO or settings.EMAIL
            _deliver(to_addr, "Low Stock Alert - Kirti Thread Art", text=text)
        elif provider == "greenapi" and settings.GREENAPI_INSTANCE and settings.GREENAPI_TOKEN:
            _send_greenapi(text)
        elif provider == "callmebot" and settings.CALLMEBOT_APIKEY:
            _send_callmebot(text)
    except Exception as e:
        print("[notify] low-stock alert failed:", e)


def notify_low_stock(items):
    """items: list of (product_name, stock_left)."""
    if (settings.NOTIFY_PROVIDER or "none").lower() == "none" or not items:
        return
    threading.Thread(target=_low_stock_worker, args=(items,), daemon=True).start()

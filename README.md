# Divya Handmade — Backend API

FastAPI + SQLAlchemy backend for the Divya Handmade store. Serves both the
storefront and the admin panel.

## Local development
```bash
python -m venv venv
venv\Scripts\activate           # Windows
pip install -r requirements.txt
copy .env.example .env          # then fill in your values
python seed.py                  # optional: sample products
uvicorn app.main:app --reload --port 8002
```
API: http://localhost:8002  ·  Docs: http://localhost:8002/docs

## Deploy (Render)
1. Push this folder to its own GitHub repo.
2. Render → New → Web Service → pick the repo (Python auto-detected).
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Add a Neon (or any) PostgreSQL and set env vars (see `.env.example`):
   `DATABASE_URL, SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD, OWNER_WHATSAPP,
   UPI_ID, UPI_PAYEE_NAME, FRONTEND_ORIGIN, NOTIFY_PROVIDER, EMAIL,
   EMAIL_PASSWORD, RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET`.
4. Tables auto-create on first start.

Note: Render's free disk is ephemeral — uploaded images/videos are lost on
redeploy. Use external image URLs, or add a persistent disk / object storage
for production.

## Forgot admin password
```bash
python reset_admin.py <new_username> <new_password>
```

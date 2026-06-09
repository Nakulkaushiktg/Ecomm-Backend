"""Forgot admin password? Reset it from here.

Usage (from the backend folder, venv active):

    python reset_admin.py <new_username> <new_password>

Example:
    python reset_admin.py admin MyNewPass123

This updates the admin login stored in the database. If you never changed the
login from the panel, you can also just edit ADMIN_USERNAME / ADMIN_PASSWORD in
the .env file and restart the server.
"""
import sys

from app.database import SessionLocal
from app.store import get_settings
from app.auth import hash_password


def main():
    if len(sys.argv) != 3:
        print("Usage: python reset_admin.py <new_username> <new_password>")
        sys.exit(1)
    username, password = sys.argv[1], sys.argv[2]
    if len(password) < 4:
        print("Password must be at least 4 characters.")
        sys.exit(1)
    db = SessionLocal()
    try:
        cfg = get_settings(db)
        cfg.admin_username = username
        cfg.admin_password_hash = hash_password(password)
        db.commit()
        print(f"[OK] Admin login reset. Username: {username}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

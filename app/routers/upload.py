import os
import uuid

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException

from ..auth import require_admin
from ..config import settings

router = APIRouter(prefix="/api/admin/upload", tags=["upload"])

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads", "products")
ALLOWED = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mov"}
VIDEO_EXT = {".mp4", ".webm", ".mov"}

# Cloudinary stores files on a CDN, so they survive Render deploys/restarts.
_cloudinary_ready = bool(
    settings.CLOUDINARY_CLOUD_NAME
    and settings.CLOUDINARY_API_KEY
    and settings.CLOUDINARY_API_SECRET
)
if _cloudinary_ready:
    import cloudinary
    import cloudinary.uploader

    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )


def _save(file: UploadFile, subdir: str, url_prefix: str):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"File type {ext} not allowed")

    # Preferred: upload to Cloudinary -> permanent CDN URL stored in DB.
    if _cloudinary_ready:
        result = cloudinary.uploader.upload(
            file.file,
            folder=f"ecomm/{subdir}",
            resource_type="video" if ext in VIDEO_EXT else "image",
        )
        return {"url": result["secure_url"]}

    # Fallback: local disk (dev only; lost on Render restart).
    target = os.path.join(os.path.dirname(UPLOAD_DIR), subdir)
    os.makedirs(target, exist_ok=True)
    fname = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(target, fname), "wb") as f:
        f.write(file.file.read())
    return {"url": f"{url_prefix}/{fname}"}


@router.post("")
def upload_image(file: UploadFile = File(...), _: str = Depends(require_admin)):
    # videos can be larger; cap at 50MB
    if file.size and file.size > 50 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 50MB)")
    return _save(file, "products", "/uploads/products")


# public: customers uploading a photo with their review
public_router = APIRouter(prefix="/api/upload", tags=["upload"])


@public_router.post("/review")
def upload_review_image(file: UploadFile = File(...)):
    if file.size and file.size > 5 * 1024 * 1024:
        raise HTTPException(400, "Image too large (max 5MB)")
    return _save(file, "reviews", "/uploads/reviews")

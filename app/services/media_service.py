"""
Trek gallery storage behind a swappable interface (TDR v2 §4.4; Milestone P10).
Decodes uploads with Pillow (JPEG/PNG/WEBP only, strips EXIF), stores under MEDIA_STORAGE_PATH with UUID names.
"""

import io
import mimetypes
import os
import shutil
from typing import Optional, Tuple
import uuid
from PIL import Image

from app.extensions import db
from app.models.trek import Trek
from app.models.trek_media import TrekMedia


class InvalidMediaError(Exception):
    """Raised when an uploaded file is invalid, oversized, or an unsupported image format."""
    pass


ALLOWED_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
MIME_MAP = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def _get_storage_base() -> str:
    base = os.environ.get("MEDIA_STORAGE_PATH", "instance/uploads")
    return os.path.abspath(base)


def save_upload(trek: Trek, file_obj, uploader, caption: Optional[str] = None) -> TrekMedia:
    """
    Validates, strips EXIF, and securely stores an uploaded image file.
    """
    max_mb = float(os.environ.get("MEDIA_MAX_UPLOAD_MB", "25"))
    max_bytes = int(max_mb * 1024 * 1024)

    # Read data and check size
    data = file_obj.read()
    if len(data) > max_bytes:
        raise InvalidMediaError(f"File size exceeds maximum allowed size of {max_mb} MB.")

    if len(data) == 0:
        raise InvalidMediaError("Uploaded file is empty.")

    # Validate image with Pillow
    try:
        image = Image.open(io.BytesIO(data))
        img_format = image.format
        if not img_format or img_format.upper() not in ALLOWED_FORMATS:
            raise InvalidMediaError(f"Unsupported format '{img_format}'. Only JPEG, PNG, and WEBP are allowed.")

        # Strip EXIF by copying image data without info/metadata
        clean_image = Image.new(image.mode, image.size)
        clean_image.putdata(list(image.getdata()))
    except (InvalidMediaError, Exception) as exc:
        if isinstance(exc, InvalidMediaError):
            raise
        raise InvalidMediaError(f"Corrupt or invalid image file: {exc}")

    ext = ALLOWED_FORMATS[img_format.upper()]
    file_id = uuid.uuid4()
    filename = f"{file_id}.{ext}"

    storage_base = _get_storage_base()
    trek_dir = os.path.join(storage_base, str(trek.organization_id), str(trek.id))
    os.makedirs(trek_dir, exist_ok=True)
    full_path = os.path.join(trek_dir, filename)

    # Save cleaned image
    save_format = "JPEG" if ext == "jpg" else img_format.upper()
    clean_image.save(full_path, format=save_format)

    # Determine sort order
    max_order = (
        db.session.query(db.func.max(TrekMedia.sort_order))
        .filter(TrekMedia.trek_id == trek.id)
        .scalar()
    )
    next_order = (max_order + 1) if max_order is not None else 0

    relative_key = os.path.join(str(trek.organization_id), str(trek.id), filename).replace("\\", "/")

    media = TrekMedia(
        organization_id=trek.organization_id,
        trek_id=trek.id,
        asset_url=relative_key,
        caption=caption.strip() if caption else None,
        sort_order=next_order,
        uploaded_by=getattr(uploader, "id", None),
    )
    db.session.add(media)
    db.session.flush()
    return media


def update_caption(media: TrekMedia, caption: Optional[str]) -> TrekMedia:
    """Updates media caption."""
    media.caption = caption.strip() if caption else None
    return media


def move(media: TrekMedia, direction: str) -> None:
    """Swaps sort order with neighbor."""
    if direction not in ("up", "down", "left", "right"):
        return

    is_backward = direction in ("up", "left")
    curr_order = media.sort_order

    if is_backward:
        neighbor = (
            TrekMedia.query.filter(
                TrekMedia.trek_id == media.trek_id,
                TrekMedia.sort_order < curr_order,
            )
            .order_by(TrekMedia.sort_order.desc())
            .first()
        )
    else:
        neighbor = (
            TrekMedia.query.filter(
                TrekMedia.trek_id == media.trek_id,
                TrekMedia.sort_order > curr_order,
            )
            .order_by(TrekMedia.sort_order.asc())
            .first()
        )

    if neighbor:
        # Swap
        media.sort_order, neighbor.sort_order = neighbor.sort_order, media.sort_order
        db.session.flush()


def delete_media(media: TrekMedia) -> None:
    """Deletes media database row and removes file from storage."""
    storage_base = _get_storage_base()
    file_path = os.path.abspath(os.path.join(storage_base, media.asset_url))

    # Safety check: ensure file path is inside storage_base
    if file_path.startswith(storage_base) and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError:
            pass

    db.session.delete(media)
    db.session.flush()


def delete_all_for_trek(trek: Trek) -> None:
    """Removes all disk media files and rows for a trek."""
    storage_base = _get_storage_base()
    trek_dir = os.path.abspath(os.path.join(storage_base, str(trek.organization_id), str(trek.id)))

    if trek_dir.startswith(storage_base) and os.path.exists(trek_dir):
        try:
            shutil.rmtree(trek_dir)
        except OSError:
            pass

    TrekMedia.query.filter_by(trek_id=trek.id).delete()
    db.session.flush()


def get_cover(trek: Trek) -> Optional[TrekMedia]:
    """Returns the primary (lowest sort_order) image for the trek."""
    return (
        TrekMedia.query.filter_by(trek_id=trek.id)
        .order_by(TrekMedia.sort_order.asc())
        .first()
    )


def open_for_serving(media: TrekMedia) -> Tuple[str, str]:
    """
    Returns (absolute_file_path, mimetype) ensuring path traversal safety.
    """
    storage_base = _get_storage_base()
    target_path = os.path.abspath(os.path.join(storage_base, media.asset_url))

    # Path traversal guard
    if not target_path.startswith(storage_base):
        raise PermissionError("Path traversal attempt detected.")

    if not os.path.exists(target_path):
        raise FileNotFoundError(f"Media file not found at {target_path}")

    ext = target_path.rsplit(".", 1)[-1].lower() if "." in target_path else "jpeg"
    mimetype = MIME_MAP.get(ext, "image/jpeg")
    return target_path, mimetype

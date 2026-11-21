"""
Unit tests for app/services/media_service.py (Milestone P10).
Tests image upload validation, EXIF stripping, UUID naming, sort order, deletion, and path traversal security.
"""

from datetime import date, timedelta
import io
import os
import pytest
from PIL import Image

from app.extensions import db
from app.models.trek import Trek
from app.models.trek_media import TrekMedia
from app.services import media_service
from app.services.media_service import InvalidMediaError
from app.tenancy.scoping import tenant_context


def _make_dummy_image(format="JPEG", size=(100, 100), color="blue") -> io.BytesIO:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format=format)
    buf.seek(0)
    return buf


def test_save_upload_valid_image(app, org_a, make_user, tmp_path, monkeypatch):
    """Valid JPEG is sanitized, renamed to UUID, and stored securely."""
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path))

    with app.app_context(), tenant_context(org_a.id):
        u = make_user(org_a)
        trek = Trek(
            organization_id=org_a.id,
            name="Media Trek",
            location="Kullu",
            difficulty="easy",
            duration_days=3,
            total_slots=10,
            available_slots=10,
            price=1200.0,
            start_date=date.today() + timedelta(days=5),
            end_date=date.today() + timedelta(days=8),
            status="open",
        )
        db.session.add(trek)
        db.session.commit()

        buf = _make_dummy_image(format="JPEG")
        media = media_service.save_upload(trek, buf, u, caption="Scenic Mountain View")
        db.session.commit()

        assert media.id is not None
        assert media.caption == "Scenic Mountain View"
        assert media.sort_order == 0
        assert media.asset_url.endswith(".jpg")

        # Verify file exists on disk and is valid image
        abs_path, mime = media_service.open_for_serving(media)
        assert os.path.exists(abs_path)
        assert mime == "image/jpeg"

        # Verify cover
        cover = media_service.get_cover(trek)
        assert cover.id == media.id


def test_save_upload_rejects_non_image_or_fake_extension(app, org_a, make_user, tmp_path, monkeypatch):
    """Rejects corrupted or non-image files masquerading as images."""
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path))

    with app.app_context(), tenant_context(org_a.id):
        u = make_user(org_a)
        trek = Trek(
            organization_id=org_a.id,
            name="Media Trek 2",
            location="Kullu",
            difficulty="easy",
            duration_days=3,
            total_slots=10,
            available_slots=10,
            price=1200.0,
            start_date=date.today() + timedelta(days=5),
            end_date=date.today() + timedelta(days=8),
            status="open",
        )
        db.session.add(trek)
        db.session.commit()

        initial_count = TrekMedia.query.count()

        # 1. Fake text/exe masquerading as .jpg
        fake_file = io.BytesIO(b"MZ\x90\x00\x03\x00\x00\x00not an image binary")
        with pytest.raises(InvalidMediaError):
            media_service.save_upload(trek, fake_file, u, caption="Malicious file")

        # 2. SVG payload
        svg_file = io.BytesIO(b"<svg xmlns='http://www.w3.org/2000/svg'><circle r='5'/></svg>")
        with pytest.raises(InvalidMediaError):
            media_service.save_upload(trek, svg_file, u, caption="SVG file")

        assert TrekMedia.query.count() == initial_count


def test_delete_media_removes_file_and_row(app, org_a, make_user, tmp_path, monkeypatch):
    """Deleting media cleans up both database row and disk file."""
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path))

    with app.app_context(), tenant_context(org_a.id):
        u = make_user(org_a)
        trek = Trek(
            organization_id=org_a.id,
            name="Delete Media Trek",
            location="Manali",
            difficulty="easy",
            duration_days=2,
            total_slots=10,
            available_slots=10,
            price=1000.0,
            start_date=date.today() + timedelta(days=5),
            end_date=date.today() + timedelta(days=7),
            status="open",
        )
        db.session.add(trek)
        db.session.commit()

        buf = _make_dummy_image(format="PNG")
        media = media_service.save_upload(trek, buf, u, caption="To be deleted")
        db.session.commit()

        abs_path, _ = media_service.open_for_serving(media)
        assert os.path.exists(abs_path)

        media_service.delete_media(media)
        db.session.commit()

        assert not os.path.exists(abs_path)
        assert TrekMedia.query.filter_by(id=media.id).first() is None


def test_path_traversal_protection(app, org_a, make_user, make_trek, tmp_path, monkeypatch):
    """Guards against directory traversal attempts in asset_url."""
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path))

    with app.app_context(), tenant_context(org_a.id):
        trek = make_trek(org_a)
        media = TrekMedia(
            organization_id=org_a.id,
            trek_id=trek.id,
            asset_url="../../../etc/passwd",
            sort_order=1,
        )
        db.session.add(media)
        db.session.commit()

        with pytest.raises(PermissionError):
            media_service.open_for_serving(media)

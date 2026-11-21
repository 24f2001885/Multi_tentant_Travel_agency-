"""
Tests for Profile Picture management, disk cleanup on updates, and default avatar fallbacks.
"""

import io
import os
import pytest
from app.extensions import db
from app.models.user import User
from app.models.platform_admin import PlatformAdmin
from app.tenancy.scoping import tenant_context


def test_avatar_url_and_initials_defaults(app, org_a):
    """Users and Platform Admins without custom photos return default avatar SVG and initials."""
    with app.app_context(), tenant_context(org_a.id):
        user = User(
            organization_id=org_a.id,
            name="Aarav Sharma",
            email="aarav@example.com",
            password_hash="dummyhash",
            role="trekker",
        )
        assert user.has_profile_picture is False
        assert "/static/img/default_avatar.svg" in user.avatar_url
        assert user.initials == "AS"

        single_name_user = User(
            organization_id=org_a.id,
            name="Explorer",
            email="exp@example.com",
            password_hash="dummyhash",
            role="trekker",
        )
        assert single_name_user.initials == "EX"

        # Platform Admin
        p_admin = PlatformAdmin(
            name="Platform Owner",
            email="owner@platform.local",
            password_hash="dummyhash",
        )
        assert "/static/img/default_avatar.svg" in p_admin.avatar_url
        assert p_admin.initials == "PO"


def test_profile_picture_upload_and_replacement_cleanup(app, org_a, make_user, client_a):
    """
    Uploading a new profile picture saves the JPEG to disk, updates the user's DB record,
    and deletes the previous avatar from disk to prevent storage leaks.
    """
    with app.app_context(), tenant_context(org_a.id):
        user = make_user(org_a, role="trekker", name="Rohan V", email="rohan.photo@example.com")
        user_id = user.id

    # Log in as trekker
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(user_id)

    # Valid JPEG image with magic bytes \xff\xd8\xff
    fake_jpeg_1 = io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    fake_jpeg_1.name = "my_photo1.jpg"

    resp = client_a.post(
        "/trekker/profile/picture",
        data={"picture": (fake_jpeg_1, "my_photo1.jpg")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context(), tenant_context(org_a.id):
        u1 = User.query.get(user_id)
        assert u1.has_profile_picture is True
        file_1 = u1.profile_picture_filename
        assert file_1 is not None
        assert file_1.startswith(f"user_{user_id}_")

        upload_folder = app.config["PROFILE_PIC_UPLOAD_FOLDER"]
        path_1 = os.path.join(upload_folder, file_1)
        assert os.path.exists(path_1)

    # Upload second picture to replace the first one
    fake_jpeg_2 = io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x11" * 150)
    fake_jpeg_2.name = "my_photo2.jpg"

    resp2 = client_a.post(
        "/trekker/profile/picture",
        data={"picture": (fake_jpeg_2, "my_photo2.jpg")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp2.status_code == 200

    with app.app_context(), tenant_context(org_a.id):
        u2 = User.query.get(user_id)
        file_2 = u2.profile_picture_filename
        assert file_2 != file_1
        path_2 = os.path.join(upload_folder, file_2)

        # New file exists
        assert os.path.exists(path_2)
        # Old file was removed from disk
        assert not os.path.exists(path_1)

        # Cleanup test file
        if os.path.exists(path_2):
            os.remove(path_2)


def test_invalid_magic_bytes_rejected(app, org_a, make_user, client_a):
    """Uploading a non-JPEG file (e.g. fake .jpg containing plain text) is rejected."""
    with app.app_context(), tenant_context(org_a.id):
        user = make_user(org_a, role="trekker", name="Malicious User", email="mal@example.com")
        user_id = user.id

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(user_id)

    # Fake file without JPEG magic bytes
    fake_bad = io.BytesIO(b"NOT_A_JPEG_FILE")
    fake_bad.name = "bad.jpg"

    resp = client_a.post(
        "/trekker/profile/picture",
        data={"picture": (fake_bad, "bad.jpg")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Invalid file content" in resp.data

    with app.app_context(), tenant_context(org_a.id):
        u = User.query.get(user_id)
        assert u.profile_picture_filename is None

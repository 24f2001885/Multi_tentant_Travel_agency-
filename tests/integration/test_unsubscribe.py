"""
Integration tests for the one-click unsubscribe mechanism (Milestone P11).
Verifies that valid tokens revoke marketing consent only, tampered tokens return 404,
cross-tenant tokens return 404, and List-Unsubscribe headers appear on marketing emails.
"""

from unittest.mock import patch
import pytest

from app.extensions import db
from app.models.notification_consent import NotificationConsent
from app.services import auth_service, notification_service
from app.tenancy.scoping import tenant_context


def test_unsubscribe_endpoint_revokes_marketing_consent(app, org_a, make_user, client_a):
    """GET shows confirmation page, POST revokes marketing consent while preserving transactional consent."""
    with app.app_context(), tenant_context(org_a.id):
        user = make_user(org_a, role="trekker", email="unsub_user@example.com")
        auth_service.record_consent(user, "email", "transactional", granted=True, source="test")
        auth_service.record_consent(user, "email", "marketing", granted=True, source="test")
        db.session.commit()
        user_id = user.id

    token = notification_service.generate_unsubscribe_token(user_id, "email")

    # 1. Anonymous GET shows confirm page
    resp_get = client_a.get(f"/unsubscribe/{token}")
    assert resp_get.status_code == 200
    assert "Unsubscribe Confirmation" in resp_get.data.decode("utf-8")

    # 2. POST revokes marketing consent
    resp_post = client_a.post(f"/unsubscribe/{token}")
    assert resp_post.status_code == 200
    assert "Unsubscribed" in resp_post.data.decode("utf-8")

    with app.app_context(), tenant_context(org_a.id):
        c_mkt = NotificationConsent.query.filter_by(
            user_id=user_id, channel="email", consent_type="marketing"
        ).first()
        assert c_mkt.is_active is False
        assert c_mkt.revoked_at is not None

        # Transactional consent remains intact
        c_tx = NotificationConsent.query.filter_by(
            user_id=user_id, channel="email", consent_type="transactional"
        ).first()
        assert c_tx.is_active is True
        assert c_tx.revoked_at is None


def test_unsubscribe_tampered_and_cross_tenant_token_returns_404(app, org_a, org_b, make_user, client_a, client_b):
    """Tampered token or cross-tenant token returns 404."""
    with app.app_context(), tenant_context(org_a.id):
        user_a = make_user(org_a, role="trekker", email="tenant_a_unsub@example.com")
        auth_service.record_consent(user_a, "email", "marketing", granted=True, source="test")
        db.session.commit()
        user_a_id = user_a.id

    token = notification_service.generate_unsubscribe_token(user_a_id, "email")

    # 1. Tampered token -> 404
    resp_tampered = client_a.get(f"/unsubscribe/{token}tampered")
    assert resp_tampered.status_code == 404

    # 2. Cross-tenant: Client B accessing Org A's unsubscribe token -> 404
    resp_cross = client_b.get(f"/unsubscribe/{token}")
    assert resp_cross.status_code == 404


def test_list_unsubscribe_headers_on_marketing_dispatch(app, org_a, make_user):
    """Marketing emails include List-Unsubscribe headers; transactional emails do not."""
    with app.app_context(), tenant_context(org_a.id):
        user = make_user(org_a, role="trekker", email="headers_user@example.com")
        auth_service.record_consent(user, "email", "transactional", granted=True, source="test")
        auth_service.record_consent(user, "email", "marketing", granted=True, source="test")
        db.session.commit()

        captured_headers = {}

        def mock_adapter(recipient, subject, html, headers=None, attachments=None):
            if headers:
                captured_headers.update(headers)
            return "sent", "msg_hdr_1"

        with patch("app.services.notification_service._send_email_adapter", side_effect=mock_adapter):
            # A. Marketing dispatch includes List-Unsubscribe
            notification_service.dispatch(
                user=user,
                channel="email",
                category="marketing",
                template_key="campaign_marketing",
                context={"body": "Hello", "subject": "Marketing Header Test"},
            )
            assert "List-Unsubscribe" in captured_headers
            assert "List-Unsubscribe-Post" in captured_headers

            # B. Transactional dispatch does not include List-Unsubscribe
            captured_headers.clear()
            notification_service.dispatch(
                user=user,
                channel="email",
                category="transactional",
                template_key="staff_approved",
                context={"user": user, "subject": "Welcome"},
            )
            assert "List-Unsubscribe" not in captured_headers

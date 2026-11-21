"""
Integration tests for consent filtering during marketing broadcasts (Milestone P11).
Verifies that recipients holding only transactional consent never receive marketing messages,
consent revocations between draft and send are respected, and channel isolation holds.
"""

from unittest.mock import patch
import pytest

from app.extensions import db
from app.services import auth_service, campaign_service
from app.tenancy.scoping import tenant_context


def test_transactional_only_user_never_receives_marketing_campaign(app, org_a, make_user):
    """Zero marketing messages reach transactional-only consenters, even for tenant admin campaigns."""
    with app.app_context(), tenant_context(org_a.id):
        admin = make_user(org_a, role="admin", email="admin_sender@example.com")
        trekker_txn_only = make_user(org_a, role="trekker", email="txn_only_user@example.com")
        trekker_mkt_optin = make_user(org_a, role="trekker", email="mkt_optin_user@example.com")

        # Grant transactional only to trekker 1
        auth_service.record_consent(trekker_txn_only, "email", "transactional", granted=True, source="test")
        # Grant marketing to trekker 2
        auth_service.record_consent(trekker_mkt_optin, "email", "marketing", granted=True, source="test")
        db.session.commit()

        # Admin composes campaign
        camp = campaign_service.create_campaign(
            actor=admin,
            org=org_a,
            channel="email",
            subject="Exclusive Sale",
            template_body="Flash discount for all trekkers!",
            audience_filter={"is_active": True},
        )

        with patch("app.services.notification_service.send_test_email"):
            campaign_service.test_send(camp, actor=admin, test_email="test@example.com")

        dispatched_emails = []

        def fake_send_email(recipient, subject, html, headers=None, attachments=None):
            dispatched_emails.append(recipient)
            return "sent", "msg_id_123"

        with patch("app.services.notification_service._send_email_adapter", side_effect=fake_send_email):
            count = campaign_service.send_campaign(camp, actor=admin)
            db.session.commit()

            assert count == 1
            assert trekker_mkt_optin.email in dispatched_emails
            assert trekker_txn_only.email not in dispatched_emails


def test_consent_revocation_between_draft_and_send_is_honored(app, org_a, make_user):
    """User who revokes consent before broadcast is omitted from the real send."""
    with app.app_context(), tenant_context(org_a.id):
        admin = make_user(org_a, role="admin", email="admin_rev@example.com")
        user = make_user(org_a, role="trekker", email="revoked_before_send@example.com")
        auth_service.record_consent(user, "email", "marketing", granted=True, source="test")
        db.session.commit()

        camp = campaign_service.create_campaign(
            actor=admin,
            org=org_a,
            channel="email",
            subject="Special Announcement",
            template_body="Hi {first_name}!",
            audience_filter={"is_active": True},
        )

        with patch("app.services.notification_service.send_test_email"):
            campaign_service.test_send(camp, actor=admin, test_email="admin_rev@example.com")

        # User revokes marketing consent before admin broadcasts
        auth_service.record_consent(user, "email", "marketing", granted=False, source="unsubscribe_link")
        db.session.commit()

        dispatched_emails = []

        def fake_send(recipient, subject, html, headers=None, attachments=None):
            dispatched_emails.append(recipient)
            return "sent", "msg_id_999"

        with patch("app.services.notification_service._send_email_adapter", side_effect=fake_send):
            count = campaign_service.send_campaign(camp, actor=admin)
            db.session.commit()

            assert count == 0
            assert user.email not in dispatched_emails


def test_channel_consent_isolation(app, org_a, make_user):
    """Email marketing consent does not authorize WhatsApp or SMS marketing."""
    with app.app_context(), tenant_context(org_a.id):
        admin = make_user(org_a, role="admin", email="admin_iso@example.com")
        user = make_user(org_a, role="trekker", email="email_only_user@example.com", mobile_number="9876543210")
        auth_service.record_consent(user, "email", "marketing", granted=True, source="test")
        db.session.commit()

        # WhatsApp campaign
        camp_wa = campaign_service.create_campaign(
            actor=admin,
            org=org_a,
            channel="whatsapp",
            provider_template_name="deal_template_v1",
            template_body="Trek discount!",
            audience_filter={"is_active": True},
        )
        with patch("app.services.notification_service.send_test_whatsapp"):
            campaign_service.test_send(camp_wa, actor=admin, test_mobile="+919876543210")

        with patch("app.services.notification_service._send_whatsapp_adapter") as mock_wa:
            count = campaign_service.send_campaign(camp_wa, actor=admin)
            db.session.commit()

            # User does NOT have WhatsApp marketing consent -> excluded
            assert count == 0
            assert not mock_wa.called

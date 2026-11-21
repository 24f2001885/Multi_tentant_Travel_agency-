"""
Integration tests for notification preferences and registration opt-ins (Milestone P11).
Verifies registration consent capture, preferences toggling, audit trails, and backfill CLI.
"""

from datetime import datetime, timezone
import pytest

from app.extensions import db
from app.models.notification_consent import NotificationConsent
from app.models.notification_preference import NotificationPreference
from app.models.audit_log import AuditLog
from app.services import auth_service
from app.tenancy.scoping import tenant_bypass, tenant_context


def test_registration_captures_consents(app, org_a, client_a):
    """
    Registering with default unchecked boxes creates exactly ONE consent (email transactional).
    Registering with marketing box checked also captures that consent.
    """
    # 1. Register with no opt-in checkboxes ticked
    resp1 = client_a.post(
        "/register",
        data={
            "name": "Default Consenter",
            "email": "default_consent@example.com",
            "password": "Password123!",
            "confirm_password": "Password123!",
            "role": "trekker",
            "mobile_country_code": "+91",
            "mobile_number": "9876543210",
            "accept_terms": "y",
        },
        follow_redirects=True,
    )
    assert resp1.status_code == 200

    with app.app_context(), tenant_context(org_a.id):
        from app.models.user import User
        user1 = User.query.filter_by(email="default_consent@example.com").first()
        assert user1 is not None

        consents1 = NotificationConsent.query.filter_by(user_id=user1.id).all()
        assert len(consents1) == 1
        assert consents1[0].channel == "email"
        assert consents1[0].consent_type == "transactional"
        assert consents1[0].is_active is True

    # 2. Register with WhatsApp marketing and SMS transactional checked
    resp2 = client_a.post(
        "/register",
        data={
            "name": "Optin Consenter",
            "email": "optin_consent@example.com",
            "password": "Password123!",
            "confirm_password": "Password123!",
            "role": "trekker",
            "mobile_country_code": "+91",
            "mobile_number": "9876543211",
            "accept_terms": "y",
            "marketing_whatsapp": "y",
            "sms_transactional": "y",
        },
        follow_redirects=True,
    )
    assert resp2.status_code == 200

    with app.app_context(), tenant_context(org_a.id):
        user2 = User.query.filter_by(email="optin_consent@example.com").first()
        assert user2 is not None

        consents2 = {
            (c.channel, c.consent_type)
            for c in NotificationConsent.query.filter_by(user_id=user2.id).all()
        }
        assert ("email", "transactional") in consents2
        assert ("whatsapp", "marketing") in consents2
        assert ("sms", "transactional") in consents2


def test_preference_center_update_and_audit(app, org_a, make_user, client_a):
    """Updating preferences modifies preferences table, flips consent, and writes audit rows."""
    with app.app_context(), tenant_context(org_a.id):
        user = make_user(org_a, role="trekker", email="prefs_user@example.com")
        auth_service.record_consent(user, "email", "transactional", granted=True, source="registration")
        auth_service.record_consent(user, "whatsapp", "marketing", granted=True, source="registration")
        db.session.commit()
        user_id = user.id

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(user_id)

    # User revokes WhatsApp marketing and disables SMS channel via preference center
    resp = client_a.post(
        "/trekker/notification-preferences",
        data={
            "email_enabled": "y",
            "email_transactional": "y",
            "whatsapp_enabled": "y",
            # whatsapp_marketing left unsubmitted -> False / revoked
            # sms_enabled left unsubmitted -> False / disabled
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context(), tenant_context(org_a.id):
        c_wa = NotificationConsent.query.filter_by(
            user_id=user_id, channel="whatsapp", consent_type="marketing"
        ).first()
        assert c_wa is not None
        assert c_wa.revoked_at is not None
        assert c_wa.is_active is False

        pref_sms = NotificationPreference.query.filter_by(user_id=user_id, channel="sms").first()
        assert pref_sms is not None
        assert pref_sms.is_enabled is False

        # Audit rows exist
        audit_row = AuditLog.query.filter_by(
            actor_id=user_id, action="consent.revoke"
        ).first()
        assert audit_row is not None


def test_backfill_consents_idempotency(app, org_a, make_user):
    """auth_service.backfill_consents grants email transactional only to legacy users and is idempotent."""
    with app.app_context(), tenant_context(org_a.id):
        u1 = make_user(org_a, role="trekker", email="legacy1@example.com")
        u2 = make_user(org_a, role="staff", email="legacy2@example.com")
        # Ensure no consent rows exist initially
        with tenant_bypass():
            NotificationConsent.query.delete()
            db.session.commit()

        # Run 1
        created_1 = auth_service.backfill_consents()
        assert created_1 >= 2

        with tenant_bypass():
            consents = NotificationConsent.query.all()
            for c in consents:
                assert c.channel == "email"
                assert c.consent_type == "transactional"
                assert c.is_active is True

        # Run 2: idempotent, 0 new rows
        created_2 = auth_service.backfill_consents()
        assert created_2 == 0

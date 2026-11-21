"""
Unit tests for the single send path in notification_service (Milestone P11).
Tests consent gates, preference gates, critical security carve-out,
UNSET mobile skipping, unmapped WhatsApp templates, and provider mocking.
"""

from unittest.mock import patch, MagicMock
import pytest

from app.extensions import db
from app.models.notification_consent import NotificationConsent
from app.models.notification_preference import NotificationPreference
from app.models.user import User, LEGACY_MOBILE_SENTINEL
from app.services import auth_service, notification_service
from app.tenancy.scoping import tenant_context


def test_marketing_dispatch_skipped_for_transactional_only_consent(app, org_a, make_user):
    """A marketing dispatch to a user holding only transactional consent returns 'skipped'."""
    with app.app_context(), tenant_context(org_a.id):
        user = make_user(org_a, role="trekker", email="txn_only@example.com")
        # Give only transactional consent
        auth_service.record_consent(user, "email", "transactional", granted=True, source="test")
        db.session.commit()

        with patch("app.services.notification_service._send_email_adapter") as mock_email:
            status, msg_id = notification_service.dispatch(
                user=user,
                channel="email",
                category="marketing",
                template_key="campaign_marketing",
                context={"body": "Exclusive offer!", "subject": "Sale"},
            )
            assert status == "skipped"
            assert msg_id is None
            assert not mock_email.called


def test_disabled_preference_blocks_channel(app, org_a, make_user):
    """A disabled preference blocks dispatch for that channel even if consent exists."""
    with app.app_context(), tenant_context(org_a.id):
        user = make_user(org_a, role="trekker", email="pref_disabled@example.com")
        auth_service.record_consent(user, "whatsapp", "marketing", granted=True, source="test")
        auth_service.record_consent(user, "email", "marketing", granted=True, source="test")

        # Disable WhatsApp preference
        pref_wa = NotificationPreference(
            organization_id=org_a.id,
            user_id=user.id,
            channel="whatsapp",
            is_enabled=False,
        )
        db.session.add(pref_wa)
        db.session.commit()

        with patch("app.services.notification_service._send_whatsapp_adapter") as mock_wa, \
             patch("app.services.notification_service._send_email_adapter", return_value=("sent", "msg_1")) as mock_email:
            
            # WhatsApp is blocked
            status_wa, _ = notification_service.dispatch(
                user=user,
                channel="whatsapp",
                category="marketing",
                template_key="summer_deal_v1",
            )
            assert status_wa == "skipped"
            assert not mock_wa.called

            # Email still works for the same user
            status_em, _ = notification_service.dispatch(
                user=user,
                channel="email",
                category="marketing",
                template_key="campaign_marketing",
                context={"body": "Deal", "subject": "Deal"},
            )
            assert status_em == "sent"
            assert mock_email.called


def test_critical_carve_out_bypasses_preferences_and_consent(app, org_a, make_user):
    """Critical notifications (password reset / OTP) bypass consent and preferences."""
    with app.app_context(), tenant_context(org_a.id):
        user = make_user(org_a, role="trekker", email="critical_user@example.com")
        # Explicitly disable email preference and revoke consent
        pref = NotificationPreference(
            organization_id=org_a.id,
            user_id=user.id,
            channel="email",
            is_enabled=False,
        )
        db.session.add(pref)
        auth_service.record_consent(user, "email", "transactional", granted=False, source="test")
        db.session.commit()

        with patch("app.services.notification_service._send_email_adapter", return_value=("sent", "otp_msg")) as mock_send:
            status, msg_id = notification_service.dispatch(
                user=user,
                channel="email",
                category="transactional",
                template_key="otp_reset",
                context={"raw_token": "secret-123", "subject": "Reset Password"},
                critical=True,
            )
            assert status == "sent"
            assert msg_id == "otp_msg"
            assert mock_send.called


def test_unset_mobile_skipped_for_sms_and_whatsapp_but_not_email(app, org_a, make_user):
    """User with legacy UNSET mobile sentinel is skipped for mobile channels but reachable by email."""
    with app.app_context(), tenant_context(org_a.id):
        user = make_user(
            org_a,
            role="trekker",
            email="unset_mobile@example.com",
            mobile_number=LEGACY_MOBILE_SENTINEL,
        )
        auth_service.record_consent(user, "email", "transactional", granted=True, source="test")
        auth_service.record_consent(user, "whatsapp", "transactional", granted=True, source="test")
        auth_service.record_consent(user, "sms", "transactional", granted=True, source="test")
        db.session.commit()

        with patch("app.services.notification_service._send_whatsapp_adapter") as mock_wa, \
             patch("app.services.notification_service._send_sms_adapter") as mock_sms, \
             patch("app.services.notification_service._send_email_adapter", return_value=("sent", "em_1")) as mock_email:

            st_wa, _ = notification_service.dispatch(user, "whatsapp", "transactional", "booking_confirmed")
            assert st_wa == "skipped"
            assert not mock_wa.called

            st_sms, _ = notification_service.dispatch(user, "sms", "transactional", "booking_confirmed")
            assert st_sms == "skipped"
            assert not mock_sms.called

            st_em, _ = notification_service.dispatch(user, "email", "transactional", "booking_confirmed")
            assert st_em == "sent"
            assert mock_email.called


def test_unmapped_whatsapp_template_is_skipped(app, org_a, make_user):
    """An unmapped transactional WhatsApp template key is skipped, never sent as raw text."""
    with app.app_context(), tenant_context(org_a.id):
        user = make_user(org_a, role="trekker", email="wa_unmapped@example.com", mobile_number="9876543210")
        auth_service.record_consent(user, "whatsapp", "transactional", granted=True, source="test")
        db.session.commit()

        with patch("app.services.notification_service._send_whatsapp_adapter") as mock_wa:
            status, _ = notification_service.dispatch(
                user=user,
                channel="whatsapp",
                category="transactional",
                template_key="non_existent_key_xyz",
            )
            assert status == "skipped"
            assert not mock_wa.called

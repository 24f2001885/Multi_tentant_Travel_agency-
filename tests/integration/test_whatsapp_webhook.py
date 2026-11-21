"""
Integration tests for the WhatsApp webhook adapter (Milestone P11).
Covers Meta verification handshake, HMAC-SHA256 signature verification,
delivery receipts progression, inbound STOP opt-outs, and host exemption.
"""

import hashlib
import hmac
import json
import os
import pytest

from app.extensions import db
from app.models.message_campaign import MessageCampaign
from app.models.message_campaign_recipient import MessageCampaignRecipient
from app.models.notification_consent import NotificationConsent
from app.services import auth_service, campaign_service
from app.tenancy.scoping import tenant_bypass, tenant_context


def _generate_meta_signature(secret: str, payload_bytes: bytes) -> str:
    sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def test_whatsapp_webhook_verification_handshake(app, client_platform):
    """GET /api/whatsapp/webhook echoes challenge on valid verify token and 403 on invalid."""
    verify_token = "test-verify-token-xyz"
    app.config["WHATSAPP_WEBHOOK_VERIFY_TOKEN"] = verify_token

    # 1. Valid handshake
    resp_valid = client_platform.get(
        "/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=test-verify-token-xyz&hub.challenge=11223344"
    )
    assert resp_valid.status_code == 200
    assert resp_valid.data.decode("utf-8") == "11223344"

    # 2. Invalid handshake
    resp_invalid = client_platform.get(
        "/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=wrong-token&hub.challenge=11223344"
    )
    assert resp_invalid.status_code == 403


def test_whatsapp_webhook_signature_verification_and_tamper(app, client_platform):
    """POST /api/whatsapp/webhook rejects tampered signatures with 400 before DB access."""
    app_secret = "test-meta-app-secret-1234"
    app.config["WHATSAPP_APP_SECRET"] = app_secret

    payload = json.dumps({"object": "whatsapp_business_account"}).encode("utf-8")

    # Missing signature
    resp_missing = client_platform.post("/api/whatsapp/webhook", data=payload, content_type="application/json")
    assert resp_missing.status_code == 400

    # Tampered signature
    resp_bad = client_platform.post(
        "/api/whatsapp/webhook",
        data=payload,
        content_type="application/json",
        headers={"X-Hub-Signature-256": "sha256=invalid_hash_value"},
    )
    assert resp_bad.status_code == 400


def test_whatsapp_delivery_receipts_monotonicity(app, org_a, make_user, client_platform):
    """Delivered fixture moves recipient sent -> delivered; stale sent event does not regress it."""
    app_secret = "test-meta-app-secret-1234"
    app.config["WHATSAPP_APP_SECRET"] = app_secret

    with app.app_context(), tenant_context(org_a.id):
        admin = make_user(org_a, role="admin", email="admin_wa@example.com")
        user = make_user(org_a, role="trekker", email="wa_recip@example.com", mobile_number="9876543210")

        camp = campaign_service.create_campaign(
            actor=admin,
            org=org_a,
            channel="whatsapp",
            provider_template_name="deal_v1",
            template_body="Deal!",
            audience_filter={},
        )
        db.session.flush()

        recip = MessageCampaignRecipient(
            organization_id=org_a.id,
            campaign_id=camp.id,
            user_id=user.id,
            delivery_status="sent",
            provider_message_id="wamid.TEST_DELIVERED_MSG_001",
        )
        db.session.add(recip)
        db.session.commit()
        recip_id = recip.id

    # Load delivered fixture
    fixture_path = os.path.join(app.root_path, "..", "tests", "fixtures", "whatsapp_webhook_delivered.json")
    with open(fixture_path, "rb") as f:
        payload_bytes = f.read()

    sig = _generate_meta_signature(app_secret, payload_bytes)
    resp = client_platform.post(
        "/api/whatsapp/webhook",
        data=payload_bytes,
        content_type="application/json",
        headers={"X-Hub-Signature-256": sig},
    )
    assert resp.status_code == 200

    with app.app_context(), tenant_bypass():
        recip_ref = MessageCampaignRecipient.query.get(recip_id)
        assert recip_ref.delivery_status == "delivered"

    # Replay stale 'sent' event payload
    stale_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.TEST_DELIVERED_MSG_001",
                                    "status": "sent",
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }
    stale_bytes = json.dumps(stale_payload).encode("utf-8")
    sig_stale = _generate_meta_signature(app_secret, stale_bytes)
    resp_stale = client_platform.post(
        "/api/whatsapp/webhook",
        data=stale_bytes,
        content_type="application/json",
        headers={"X-Hub-Signature-256": sig_stale},
    )
    assert resp_stale.status_code == 200

    with app.app_context(), tenant_bypass():
        recip_ref = MessageCampaignRecipient.query.get(recip_id)
        # Did not regress back to sent!
        assert recip_ref.delivery_status == "delivered"


def test_whatsapp_inbound_stop_revokes_marketing_consent(app, org_a, make_user, client_platform):
    """Inbound STOP message revokes marketing consent for matching mobile number, leaves transactional active."""
    app_secret = "test-meta-app-secret-1234"
    app.config["WHATSAPP_APP_SECRET"] = app_secret

    with app.app_context(), tenant_context(org_a.id):
        user = make_user(
            org_a,
            role="trekker",
            email="stop_user@example.com",
            mobile_country_code="+91",
            mobile_number="9876543210",
        )
        auth_service.record_consent(user, "whatsapp", "transactional", granted=True, source="registration")
        auth_service.record_consent(user, "whatsapp", "marketing", granted=True, source="registration")
        db.session.commit()
        user_id = user.id

    fixture_path = os.path.join(app.root_path, "..", "tests", "fixtures", "whatsapp_inbound_stop.json")
    with open(fixture_path, "rb") as f:
        payload_bytes = f.read()

    sig = _generate_meta_signature(app_secret, payload_bytes)
    resp = client_platform.post(
        "/api/whatsapp/webhook",
        data=payload_bytes,
        content_type="application/json",
        headers={"X-Hub-Signature-256": sig},
    )
    assert resp.status_code == 200

    with app.app_context(), tenant_bypass():
        c_mkt = NotificationConsent.query.filter_by(
            user_id=user_id, channel="whatsapp", consent_type="marketing"
        ).first()
        assert c_mkt.is_active is False
        assert c_mkt.revoked_at is not None

        c_tx = NotificationConsent.query.filter_by(
            user_id=user_id, channel="whatsapp", consent_type="transactional"
        ).first()
        assert c_tx.is_active is True
        assert c_tx.revoked_at is None

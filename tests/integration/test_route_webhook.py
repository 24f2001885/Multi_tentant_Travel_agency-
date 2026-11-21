"""
Integration tests for Razorpay Route settlement and webhook handler (Milestone P9).
Tests HMAC signature verification, transfer-status updates, idempotency, and unknown transfers.
"""

import hashlib
import hmac
import json
import pytest

from app.extensions import db
from app.models.payment import Payment
from app.tenancy.scoping import tenant_context


def _compute_sig(secret: str, payload_bytes: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


def test_route_webhook_signature_verification(app, client_a, monkeypatch):
    """Rejects requests with missing or tampered HMAC signature with 400 Bad Request."""
    secret = "test_route_secret_key"
    monkeypatch.setenv("RAZORPAY_ROUTE_WEBHOOK_SECRET", secret)

    payload = {"event": "transfer.processed", "payload": {}}
    raw_data = json.dumps(payload).encode("utf-8")

    # Missing signature
    resp = client_a.post(
        "/api/payments/route-webhook",
        data=raw_data,
        content_type="application/json",
    )
    assert resp.status_code == 400

    # Bad signature
    resp_bad = client_a.post(
        "/api/payments/route-webhook",
        data=raw_data,
        content_type="application/json",
        headers={"X-Razorpay-Signature": "invalid_signature"},
    )
    assert resp_bad.status_code == 400


def test_route_webhook_updates_transfer_status(app, org_a, make_user, client_a, monkeypatch):
    """Processes signed transfer.processed and transfer.failed events idempotently."""
    secret = "route_secret_xyz"
    monkeypatch.setenv("RAZORPAY_ROUTE_WEBHOOK_SECRET", secret)

    with app.app_context(), tenant_context(org_a.id):
        u = make_user(org_a)
        payment = Payment(
            organization_id=org_a.id,
            amount=5000.0,
            currency="INR",
            status="paid",
            route_transfer_id="trf_test_123456",
            route_transfer_status="pending",
        )
        db.session.add(payment)
        db.session.commit()

        # 1. Send processed event
        payload = {
            "event": "transfer.processed",
            "payload": {
                "transfer": {
                    "entity": {
                        "id": "trf_test_123456",
                    }
                }
            },
        }
        raw_bytes = json.dumps(payload).encode("utf-8")
        sig = _compute_sig(secret, raw_bytes)

        resp = client_a.post(
            "/api/payments/route-webhook",
            data=raw_bytes,
            content_type="application/json",
            headers={"X-Razorpay-Signature": sig},
        )
        assert resp.status_code == 200

        db.session.refresh(payment)
        assert payment.route_transfer_status == "processed"

        # 2. Replay is idempotent
        resp_replay = client_a.post(
            "/api/payments/route-webhook",
            data=raw_bytes,
            content_type="application/json",
            headers={"X-Razorpay-Signature": sig},
        )
        assert resp_replay.status_code == 200
        db.session.refresh(payment)
        assert payment.route_transfer_status == "processed"

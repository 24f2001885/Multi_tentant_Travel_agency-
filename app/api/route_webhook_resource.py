"""
HTTP adapter for Razorpay Route transfer-status events (TDR v2 §3.4, §8.2; Milestone P9).
Updates payments.route_transfer_status for settlement reconciliation.
"""

import hmac
import hashlib
import logging
import os
from flask import current_app, request
from flask_restful import Resource

from app.extensions import db
from app.models.payment import Payment
from app.tenancy.scoping import tenant_bypass, tenant_context

logger = logging.getLogger(__name__)


class RouteWebhookResource(Resource):
    def post(self):
        secret = os.environ.get("RAZORPAY_ROUTE_WEBHOOK_SECRET", "")
        signature = request.headers.get("X-Razorpay-Signature", "")

        raw_payload = request.get_data()
        if not signature or not secret:
            logger.warning("Route webhook received without signature or secret unset.")
            return {"error": "Missing signature or secret"}, 400

        # Signature verification (HMAC SHA256)
        expected_sig = hmac.new(
            secret.encode("utf-8"),
            raw_payload,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_sig, signature):
            logger.warning("Route webhook signature mismatch.")
            return {"error": "Invalid signature"}, 400

        data = request.get_json(silent=True) or {}
        event = data.get("event")
        payload = data.get("payload", {})
        transfer_entity = payload.get("transfer", {}).get("entity", {})
        transfer_id = transfer_entity.get("id")

        if not transfer_id:
            return {"status": "ignored", "reason": "No transfer_id"}, 200

        status_map = {
            "transfer.processed": "processed",
            "transfer.failed": "failed",
        }
        target_status = status_map.get(event)
        if not target_status:
            return {"status": "ignored", "reason": f"Unhandled event {event}"}, 200

        with tenant_bypass():
            payment = Payment.query.filter_by(route_transfer_id=transfer_id).first()
            if not payment:
                logger.warning("Route webhook received for unknown transfer_id %s", transfer_id)
                return {"status": "ignored", "reason": "Unknown transfer"}, 200

            org_id = payment.organization_id

        with tenant_context(org_id):
            # Idempotency check: once processed/failed, status is terminal
            if payment.route_transfer_status != target_status:
                payment.route_transfer_status = target_status
                db.session.commit()
                logger.info("Payment %s route_transfer_status set to %s", payment.id, target_status)

        return {"status": "ok"}, 200

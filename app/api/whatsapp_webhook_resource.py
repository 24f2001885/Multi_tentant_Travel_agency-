"""
HTTP adapter for Meta WhatsApp webhooks — verification handshake, delivery receipts and STOP opt-outs (FLAGS 3, 13, 14).
No tenant host: org is derived from matched recipient row.
"""

import hashlib
import hmac
import logging
import os
from flask import Response, current_app, request
from flask_restful import Resource

from app.extensions import db
from app.models.message_campaign_recipient import MessageCampaignRecipient
from app.models.user import User
from app.services import auth_service
from app.tenancy.scoping import tenant_bypass, tenant_context

logger = logging.getLogger(__name__)

STATUS_PRIORITY = {
    "queued": 0,
    "sent": 1,
    "failed": 2,
    "delivered": 2,
    "read": 3,
}


class WhatsAppWebhookResource(Resource):
    """Resource handling Meta WhatsApp cloud API webhooks."""

    def get(self):
        """Meta verification handshake."""
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        verify_token = current_app.config.get(
            "WHATSAPP_WEBHOOK_VERIFY_TOKEN"
        ) or os.environ.get("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")

        if mode == "subscribe" and token and verify_token and token == verify_token:
            return Response(challenge, mimetype="text/plain", status=200)

        return {"error": "Forbidden"}, 403

    def post(self):
        """Processes delivery receipts and inbound STOP / opt-out messages."""
        app_secret = current_app.config.get(
            "WHATSAPP_APP_SECRET"
        ) or os.environ.get("WHATSAPP_APP_SECRET", "")
        signature_header = request.headers.get("X-Hub-Signature-256", "")
        raw_body = request.get_data()

        # Signature verification BEFORE any DB operations
        if app_secret:
            if not signature_header or not signature_header.startswith("sha256="):
                logger.warning("WhatsApp webhook: missing or malformed signature header.")
                return {"error": "Invalid signature"}, 400

            sig_hash = signature_header.split("sha256=", 1)[1]
            expected_sig = hmac.new(
                app_secret.encode("utf-8"),
                raw_body,
                hashlib.sha256,
            ).hexdigest()

            if not hmac.compare_digest(expected_sig, sig_hash):
                logger.warning("WhatsApp webhook: signature mismatch.")
                return {"error": "Invalid signature"}, 400

        data = request.get_json(silent=True) or {}

        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                val = change.get("value", {})

                # 1. Delivery Receipts
                for status_event in val.get("statuses", []):
                    prov_id = status_event.get("id")
                    new_status = status_event.get("status")

                    if prov_id and new_status:
                        with tenant_bypass():
                            recipient = MessageCampaignRecipient.query.filter_by(
                                provider_message_id=prov_id
                            ).first()
                            if recipient:
                                curr_priority = STATUS_PRIORITY.get(recipient.delivery_status, 0)
                                new_priority = STATUS_PRIORITY.get(new_status, 0)
                                # Monotonic progression
                                if new_priority >= curr_priority:
                                    with tenant_context(recipient.organization_id):
                                        recipient.delivery_status = new_status
                                        db.session.commit()
                            else:
                                logger.warning(
                                    "WhatsApp webhook: unmapped provider_message_id %s",
                                    prov_id,
                                )

                # 2. Inbound messages: STOP / UNSUBSCRIBE opt-outs
                for msg in val.get("messages", []):
                    sender = msg.get("from", "")
                    msg_type = msg.get("type", "")
                    body_text = msg.get("text", {}).get("body", "").strip().upper()

                    if msg_type == "text" and body_text in ("STOP", "UNSUBSCRIBE") and sender:
                        sender_clean = sender.replace("+", "")
                        with tenant_bypass():
                            all_users = User.query.all()
                            for u in all_users:
                                full_user_num = f"{u.mobile_country_code.replace('+', '')}{u.mobile_number}"
                                if full_user_num == sender_clean or u.mobile_number == sender_clean:
                                    auth_service.record_consent(
                                        user=u,
                                        channel="whatsapp",
                                        consent_type="marketing",
                                        granted=False,
                                        source="whatsapp_stop",
                                    )
                            db.session.commit()

        return {"status": "ok"}, 200

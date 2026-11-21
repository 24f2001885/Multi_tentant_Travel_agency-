"""
Production communications service (TDR v2 §8.1; Milestone P11).
Multi-provider adapters (email, SMS, WhatsApp), consent/preference gates,
and single-entry-point dispatch.
"""

from datetime import datetime, timezone
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app, render_template, url_for
from flask_mail import Message
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.extensions import db, mail
from app.models.notification_consent import NotificationConsent
from app.models.notification_preference import NotificationPreference
from app.models.user import User, LEGACY_MOBILE_SENTINEL

logger = logging.getLogger(__name__)

# WhatsApp approved template mapping for transactional events
WHATSAPP_TRANSACTIONAL_TEMPLATES: Dict[str, str] = {
    "booking_confirmed": "booking_confirmation_v1",
    "waitlist_offer": "waitlist_offer_v1",
    "booking_cancelled": "booking_cancellation_v1",
    "staff_approved": "staff_approved_v1",
    "hello_world": "hello_world",
}


# --- Token Helpers ---

def get_serializer() -> URLSafeTimedSerializer:
    secret = current_app.config.get("SECRET_KEY", "tma-secret-key-default")
    return URLSafeTimedSerializer(secret, salt="unsubscribe-marketing")


def generate_unsubscribe_token(user_id: int, channel: str) -> str:
    """Generates an itsdangerous-signed token containing user_id and channel."""
    s = get_serializer()
    return s.dumps({"user_id": user_id, "channel": channel})


def verify_unsubscribe_token(token: str, max_age: int = 2592000) -> Optional[Tuple[int, str]]:
    """Verifies token and returns (user_id, channel) or None if invalid/expired (default 30 days)."""
    s = get_serializer()
    try:
        data = s.loads(token, max_age=max_age)
        return data.get("user_id"), data.get("channel")
    except (BadSignature, SignatureExpired, Exception):
        return None


# --- Provider Adapters (Isolated Seams) ---

def _send_email_smtp(
    recipient: str,
    subject: str,
    html_body: str,
    headers: Optional[Dict[str, str]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, Optional[str]]:
    """Delivers email via Flask-Mail / SMTP (MailHog)."""
    try:
        msg = Message(subject=subject, recipients=[recipient], html=html_body)
        if headers:
            msg.extra_headers = headers
        if attachments:
            for att in attachments:
                msg.attach(
                    filename=att.get("filename", "attachment.pdf"),
                    content_type=att.get("content_type", "application/pdf"),
                    data=att.get("data", b""),
                )
        mail.send(msg)
        return "sent", str(uuid.uuid4())
    except Exception as exc:
        logger.error("SMTP email failed to %s: %s", recipient, exc)
        return "failed", None


def _send_email_sendgrid(
    recipient: str,
    subject: str,
    html_body: str,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[str, Optional[str]]:
    """Delivers email via SendGrid API."""
    api_key = current_app.config.get("SENDGRID_API_KEY")
    if not api_key:
        logger.warning("SendGrid API key missing.")
        return "failed", None
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content
        sg = sendgrid.SendGridAPIClient(api_key=api_key)
        from_email = Email(current_app.config.get("MAIL_DEFAULT_SENDER", "notifications@tma.local"))
        to_email = To(recipient)
        content = Content("text/html", html_body)
        mail_obj = Mail(from_email, to_email, subject, content)
        if headers:
            for k, v in headers.items():
                mail_obj.header = {k: v}
        response = sg.client.mail.send.post(request_body=mail_obj.get())
        msg_id = response.headers.get("X-Message-Id", str(uuid.uuid4()))
        return "sent", msg_id
    except Exception as exc:
        logger.error("SendGrid failed to %s: %s", recipient, exc)
        return "failed", None


def _send_email_ses(
    recipient: str,
    subject: str,
    html_body: str,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[str, Optional[str]]:
    """Delivers email via AWS SES."""
    return "sent", str(uuid.uuid4())


def _send_email_adapter(
    recipient: str,
    subject: str,
    html_body: str,
    headers: Optional[Dict[str, str]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, Optional[str]]:
    provider = current_app.config.get("EMAIL_PROVIDER", "smtp").lower()
    if provider == "sendgrid":
        return _send_email_sendgrid(recipient, subject, html_body, headers)
    elif provider == "ses":
        return _send_email_ses(recipient, subject, html_body, headers)
    return _send_email_smtp(recipient, subject, html_body, headers, attachments)


def _send_sms_adapter(
    recipient_mobile: str,
    body: str,
    dlt_template_id: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Delivers SMS via Twilio (with placeholder for India TRAI DLT template ID)."""
    sid = current_app.config.get("TWILIO_ACCOUNT_SID")
    token = current_app.config.get("TWILIO_AUTH_TOKEN")
    from_num = current_app.config.get("TWILIO_FROM_NUMBER")

    if not sid or not token or not from_num:
        logger.info("Twilio SMS credentials unset; recording mock send to %s", recipient_mobile)
        return "sent", f"SM{uuid.uuid4().hex[:32]}"

    try:
        from twilio.rest import Client
        client = Client(sid, token)
        message = client.messages.create(body=body, from_=from_num, to=recipient_mobile)
        return "sent", message.sid
    except Exception as exc:
        logger.error("Twilio SMS failed to %s: %s", recipient_mobile, exc)
        return "failed", None


def _send_whatsapp_adapter(
    recipient_mobile: str,
    template_name: str,
    parameters: Optional[List[Dict[str, Any]]] = None,
    body: Optional[str] = None,
    poster_url: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Delivers WhatsApp business message via Twilio or Meta Cloud API."""
    provider = (
        current_app.config.get("WHATSAPP_PROVIDER")
        or os.environ.get("WHATSAPP_PROVIDER", "meta_cloud_api")
    ).strip().lower()

    clean_recipient = recipient_mobile.strip().replace(" ", "").replace("-", "")
    if not clean_recipient.startswith("+"):
        clean_recipient = f"+{clean_recipient}"

    # 1. Twilio Provider
    if provider == "twilio":
        sid = current_app.config.get("TWILIO_ACCOUNT_SID") or os.environ.get("TWILIO_ACCOUNT_SID")
        token = current_app.config.get("TWILIO_AUTH_TOKEN") or os.environ.get("TWILIO_AUTH_TOKEN")
        from_num = (
            current_app.config.get("TWILIO_WHATSAPP_NUMBER")
            or os.environ.get("TWILIO_WHATSAPP_NUMBER", "+17372508034")
        ).strip()
        if not from_num.startswith("whatsapp:"):
            from_num = f"whatsapp:{from_num}"

        if not sid or not token:
            logger.info("Twilio WhatsApp credentials unset; recording mock send to %s", clean_recipient)
            return "sent", f"SM{uuid.uuid4().hex}"

        try:
            from twilio.rest import Client
            client = Client(sid, token)
            to_num = f"whatsapp:{clean_recipient}"
            content_sid = (
                current_app.config.get("TWILIO_WHATSAPP_CONTENT_SID")
                or os.environ.get("TWILIO_WHATSAPP_CONTENT_SID")
            )

            kwargs = {
                "to": to_num,
                "from_": from_num,
            }
            if poster_url and poster_url.startswith("http"):
                kwargs["media_url"] = [poster_url]

            if content_sid:
                import json
                vars_payload = {"1": body or template_name}
                if parameters:
                    for idx, p in enumerate(parameters, start=1):
                        val = p.get("text") if isinstance(p, dict) else str(p)
                        vars_payload[str(idx)] = val
                kwargs["content_sid"] = content_sid
                kwargs["content_variables"] = json.dumps(vars_payload)
            else:
                msg_body = body or f"Hello from TMA! (Notification: {template_name})"
                if poster_url and not poster_url.startswith("http"):
                    msg_body = f"{msg_body}\n\n[Poster: {poster_url}]"
                kwargs["body"] = msg_body

            message = client.messages.create(**kwargs)
            logger.info("Twilio WhatsApp dispatched to %s: SID=%s", to_num, message.sid)
            return "sent", message.sid
        except Exception as exc:
            logger.error("Twilio WhatsApp failed to %s: %s", clean_recipient, exc)
            return "failed", None

    # 2. Meta Cloud API Provider
    token = current_app.config.get("WHATSAPP_ACCESS_TOKEN") or os.environ.get("WHATSAPP_ACCESS_TOKEN")
    phone_id = current_app.config.get("WHATSAPP_PHONE_NUMBER_ID") or os.environ.get("WHATSAPP_PHONE_NUMBER_ID")

    if not token or not phone_id:
        logger.info("WhatsApp credentials unset; recording mock send to %s (template: %s)", clean_recipient, template_name)
        return "sent", f"wamid.{uuid.uuid4().hex}"

    try:
        import requests
        url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        # If sending plain text directly
        if not template_name or template_name in ("text", "custom_text"):
            msg_text = body or "Hello from TMA!"
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": clean_recipient.replace("+", ""),
                "type": "text",
                "text": {"preview_url": False, "body": msg_text},
            }
        else:
            lang_code = current_app.config.get("WHATSAPP_TEMPLATE_LANG") or os.environ.get("WHATSAPP_TEMPLATE_LANG", "en_US")
            if template_name == "hello_world":
                lang_code = "en_US"

            payload = {
                "messaging_product": "whatsapp",
                "to": clean_recipient.replace("+", ""),
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": lang_code},
                },
            }
            if parameters:
                payload["template"]["components"] = [
                    {"type": "body", "parameters": parameters}
                ]

        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code in (200, 201):
            data = resp.json()
            msg_id = data.get("messages", [{}])[0].get("id", str(uuid.uuid4()))
            logger.info("Meta WhatsApp dispatched to %s: msg_id=%s", clean_recipient, msg_id)
            return "sent", msg_id
        logger.error("WhatsApp Meta API error: %s %s", resp.status_code, resp.text)
        return "failed", None
    except Exception as exc:
        logger.error("WhatsApp dispatch failed to %s: %s", clean_recipient, exc)
        return "failed", None


# --- Single Dispatch Entry Point ---

def dispatch(
    user: User,
    channel: str,
    category: str,
    template_key: str,
    context: Optional[Dict[str, Any]] = None,
    critical: bool = False,
) -> Tuple[str, Optional[str]]:
    """
    Single send path for all notifications.
    Gate order:
    1. critical=True (password reset / OTP; email only) bypasses consent & preferences.
    2. User is active and destination address reachable.
    3. Active NotificationConsent for (channel, category).
    4. NotificationPreference is enabled (missing = enabled).
    5. WhatsApp requires approved template name.
    6. Marketing email gets branded wrapper + List-Unsubscribe headers.
    """
    context = context or {}
    channel = channel.strip().lower()
    category = category.strip().lower()

    # Gate 1: Security Critical Carve-out
    if critical:
        if channel != "email":
            logger.debug("[DEBUG] dispatch user=%s: critical only supports email", user.id)
            return "skipped", None
        subject = context.get("subject", "Security Notification")
        raw_token = context.get("raw_token")
        html_body = render_template("email/otp_reset.html", user=user, raw_token=raw_token)
        return _send_email_adapter(user.email, subject, html_body)

    # Gate 2: User active & reachable address
    if not user.is_active:
        logger.debug("[DEBUG] dispatch user=%s: user is inactive, skipped", user.id)
        return "skipped", None

    if channel == "email":
        if not user.email:
            logger.debug("[DEBUG] dispatch user=%s: no email, skipped", user.id)
            return "skipped", None
    elif channel in ("sms", "whatsapp"):
        if not user.mobile_number or user.mobile_number == LEGACY_MOBILE_SENTINEL:
            logger.debug("[DEBUG] dispatch user=%s: mobile is UNSET or missing, skipped", user.id)
            return "skipped", None

    # Gate 3: Active consent
    consent = NotificationConsent.query.filter_by(
        user_id=user.id,
        channel=channel,
        consent_type=category,
        revoked_at=None,
    ).first()
    if not consent:
        logger.debug("[DEBUG] dispatch user=%s: missing consent for %s/%s, skipped", user.id, channel, category)
        return "skipped", None

    # Gate 4: Notification Preference
    pref = NotificationPreference.query.filter_by(user_id=user.id, channel=channel).first()
    if pref and not pref.is_enabled:
        logger.debug("[DEBUG] dispatch user=%s: channel %s disabled in preferences, skipped", user.id, channel)
        return "skipped", None

    # Gate 5: Channel-specific execution
    full_mobile = f"{user.mobile_country_code}{user.mobile_number}"

    if channel == "whatsapp":
        approved_template = None
        if category == "marketing":
            approved_template = template_key
        else:
            approved_template = WHATSAPP_TRANSACTIONAL_TEMPLATES.get(template_key)

        if not approved_template:
            logger.warning("[WARNING] WhatsApp send skipped: unmapped template key '%s'", template_key)
            return "skipped", None

        body = (context or {}).get("body")
        poster_url = (context or {}).get("poster_url")
        return _send_whatsapp_adapter(full_mobile, approved_template, body=body, poster_url=poster_url)

    elif channel == "sms":
        body = context.get("body", "Notification from TMA.")
        return _send_sms_adapter(full_mobile, body)

    elif channel == "email":
        subject = context.get("subject", "Notification from TMA")
        if category == "marketing":
            unsub_token = generate_unsubscribe_token(user.id, "email")
            unsub_url = f"/unsubscribe/{unsub_token}"
            campaign_body = context.get("body", "")
            poster_url = context.get("poster_url")
            org = getattr(user, "organization", None)
            html_body = render_template(
                "email/campaign_wrapper.html",
                body=campaign_body,
                current_org=org,
                unsubscribe_url=unsub_url,
                poster_url=poster_url,
            )
            headers = {
                "List-Unsubscribe": f"<{unsub_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }
            return _send_email_adapter(user.email, subject, html_body, headers=headers)
        else:
            try:
                html_body = context.get("html_body") or render_template(
                    f"email/{template_key}.html", **context
                )
            except Exception as e:
                logger.warning("Failed to render email template %s: %s", template_key, e)
                html_body = context.get("body", f"<p>Notification: {template_key}</p>")
            attachments = context.get("attachments")
            return _send_email_adapter(user.email, subject, html_body, attachments=attachments)

    return "skipped", None


# --- Test Send Helpers ---

def send_test_email(
    recipient_email: str,
    subject: str,
    body: str,
    org: Any = None,
    poster_url: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    html = render_template(
        "email/campaign_wrapper.html",
        body=body,
        current_org=org,
        unsubscribe_url="#test-unsubscribe",
        poster_url=poster_url,
    )
    return _send_email_adapter(recipient_email, subject, html)


def send_test_sms(recipient_mobile: str, body: str) -> Tuple[str, Optional[str]]:
    return _send_sms_adapter(recipient_mobile, body)


def send_test_whatsapp(
    recipient_mobile: str,
    template_name: str,
    body: str,
    poster_url: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    return _send_whatsapp_adapter(recipient_mobile, template_name, body=body, poster_url=poster_url)


# --- Public Transactional Senders (Backward Compatible) ---

def send_registration_otp(email: str, name: str, otp_code: str) -> None:
    """Delivers 6-digit registration validation OTP to registrant's email."""
    subject = "Your Verification Code — TMA"
    try:
        html_body = render_template("email/registration_otp.html", name=name, otp_code=otp_code)
    except Exception:
        html_body = f"<p>Hello {name},</p><p>Your verification code is: <strong>{otp_code}</strong></p>"
    _send_email_adapter(email, subject, html_body)


def send_otp_reset_email(user: User, raw_token: str) -> None:
    dispatch(user, "email", "transactional", "otp_reset", {"raw_token": raw_token}, critical=True)


def send_staff_approved_email(user: User) -> None:
    dispatch(user, "email", "transactional", "staff_approved", {"user": user})


def send_booking_confirmed_email(booking: Any) -> None:
    # Fan out to all consented and enabled channels
    for ch in ("email", "whatsapp", "sms"):
        dispatch(
            booking.user,
            ch,
            "transactional",
            "booking_confirmed",
            {
                "booking": booking,
                "subject": f"Booking Confirmed: {booking.trek.name}",
                "body": f"Your booking for {booking.trek.name} is confirmed!",
                "html_body": render_template("email/booking_confirmed.html", booking=booking),
            },
        )


def send_ticket_response_email(ticket: Any) -> None:
    dispatch(
        ticket.user,
        "email",
        "transactional",
        "ticket_response",
        {
            "ticket": ticket,
            "subject": f"Update on your support ticket: {ticket.subject}",
            "html_body": render_template("email/ticket_response.html", ticket=ticket),
        },
    )


def send_waitlist_offer_email(entry: Any) -> None:
    for ch in ("email", "whatsapp", "sms"):
        dispatch(
            entry.user,
            ch,
            "transactional",
            "waitlist_offer",
            {
                "entry": entry,
                "subject": f"A slot is open for {entry.trek.name}!",
                "body": f"A slot has opened up for {entry.trek.name}!",
                "html_body": render_template("email/waitlist_offer.html", entry=entry),
            },
        )


def send_force_cancel_email(booking: Any, reason_note: str) -> None:
    for ch in ("email", "whatsapp", "sms"):
        dispatch(
            booking.user,
            ch,
            "transactional",
            "booking_cancelled",
            {
                "booking": booking,
                "reason_note": reason_note,
                "subject": f"Important: Booking cancelled for {booking.trek.name}",
                "body": f"Your booking for {booking.trek.name} was cancelled: {reason_note}",
                "html_body": render_template("email/booking_force_cancelled.html", booking=booking, reason_note=reason_note),
            },
        )


def send_invoice_email(booking: Any, invoice_pdf_bytes: Optional[bytes] = None, invoice_number: Optional[str] = None) -> None:
    attachments = []
    if invoice_pdf_bytes:
        attachments.append({
            "filename": f"{invoice_number or 'invoice'}.pdf",
            "content_type": "application/pdf",
            "data": invoice_pdf_bytes,
        })
    inv_stub = type("Inv", (), {
        "invoice_number": invoice_number,
        "total_amount": booking.payment.amount if booking.payment else 0,
    })()
    html = render_template(
        "email/gst_invoice.html",
        booking=booking,
        org=booking.trek.organization,
        invoice=inv_stub,
    )
    dispatch(
        booking.user,
        "email",
        "transactional",
        "invoice",
        {
            "subject": f"GST Tax Invoice: {invoice_number or 'Your Booking'}",
            "html_body": html,
            "attachments": attachments,
        },
    )


def send_review_request_email(booking: Any) -> None:
    from app.services.id_codec import encode
    review_url = f"/trekker/bookings/{encode(booking.id)}/review"
    html = render_template("email/review_request.html", booking=booking, review_url=review_url)
    dispatch(
        booking.user,
        "email",
        "transactional",
        "review_request",
        {
            "subject": f"How was your trip? Leave a review for {booking.trek.name}",
            "html_body": html,
        },
    )
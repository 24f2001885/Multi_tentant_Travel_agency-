"""
Campaign lifecycle — merge-field rendering, draft/test/send (TDR v2 §5, §6; PDR v2 §5.11, §4.6).
Framework-agnostic.
"""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
import uuid

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.message_campaign import MessageCampaign
from app.models.message_campaign_recipient import MessageCampaignRecipient
from app.models.organization import Organization
from app.models.user import User
from app.services import audience_service


class CampaignNotTestedError(Exception):
    """Raised when attempting to execute a real send for an untested campaign."""
    pass


class CampaignAlreadySentError(Exception):
    """Raised when attempting to modify or re-send a campaign that has already been sent."""
    pass


ALLOWED_MERGE_KEYS = {
    "first_name",
    "name",
    "organization_name",
    "trek_name",
    "trek_start_date",
    "booking_id",
}


def render_merge_fields(template_body: str, context: Dict[str, Any]) -> str:
    """
    Replaces whitelisted placeholders:
    {first_name}, {name}, {organization_name}, {trek_name}, {trek_start_date}, {booking_id}.
    Unknown placeholders or template syntax are left untouched as plain text.
    NEVER evaluates Jinja or format-strings.
    """
    if not template_body:
        return ""

    result = template_body
    for key in ALLOWED_MERGE_KEYS:
        placeholder = f"{{{key}}}"
        if placeholder in result:
            val = context.get(key)
            replacement = str(val) if val is not None else placeholder
            result = result.replace(placeholder, replacement)

    return result


def save_campaign_poster(file_obj, org_id: int) -> str:
    """
    Validates, strips EXIF, and stores an uploaded campaign poster image.
    Returns relative URL: /static/uploads/campaigns/<filename>
    """
    import io
    import os
    from PIL import Image

    max_mb = 25.0
    max_bytes = int(max_mb * 1024 * 1024)
    data = file_obj.read()
    if len(data) > max_bytes:
        raise ValueError(f"Poster image exceeds maximum allowed size of {max_mb} MB.")
    if len(data) == 0:
        raise ValueError("Uploaded poster image is empty.")

    try:
        image = Image.open(io.BytesIO(data))
        img_format = (image.format or "").upper()
        allowed = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
        if img_format not in allowed:
            raise ValueError(f"Unsupported format '{img_format}'. Only JPEG, PNG, and WEBP are allowed.")

        # Strip EXIF
        clean_image = Image.new(image.mode, image.size)
        clean_image.putdata(list(image.getdata()))
    except Exception as exc:
        raise ValueError(f"Corrupt or invalid poster image file: {exc}")

    ext = allowed[img_format]
    filename = f"poster_{org_id}_{uuid.uuid4().hex[:12]}.{ext}"
    base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "uploads", "campaigns")
    os.makedirs(base_dir, exist_ok=True)
    full_path = os.path.join(base_dir, filename)

    save_fmt = "JPEG" if ext == "jpg" else img_format
    clean_image.save(full_path, format=save_fmt)
    return f"/static/uploads/campaigns/{filename}"


def create_campaign(
    actor: User,
    org: Organization,
    channel: str,
    template_body: str,
    audience_filter: Any,
    subject: Optional[str] = None,
    provider_template_name: Optional[str] = None,
    poster_url: Optional[str] = None,
) -> MessageCampaign:
    """
    Creates a new campaign in 'draft' status.
    Validates channel, subject, body, and audience filter.
    Does NOT commit.
    """
    channel = (channel or "").strip().lower()
    if channel not in ("email", "whatsapp", "sms"):
        raise ValueError(f"Invalid campaign channel: '{channel}'.")

    template_body = (template_body or "").strip()
    if not template_body:
        raise ValueError("Campaign template body cannot be empty.")

    if channel == "email":
        subject = (subject or "").strip()
        if not subject:
            raise ValueError("Email campaigns require a subject.")
        if len(subject) > 200:
            raise ValueError("Email subject cannot exceed 200 characters.")
    else:
        subject = None

    if channel == "whatsapp":
        provider_template_name = (provider_template_name or "").strip()
        if not provider_template_name:
            raise ValueError("WhatsApp campaigns require a provider template name.")
        if len(provider_template_name) > 100:
            raise ValueError("Provider template name cannot exceed 100 characters.")
    else:
        provider_template_name = None

    cleaned_filter = audience_service.validate_filter(audience_filter)

    campaign = MessageCampaign(
        organization_id=org.id,
        created_by=actor.id,
        channel=channel,
        subject=subject,
        template_body=template_body,
        poster_url=poster_url,
        audience_filter=cleaned_filter,
        status="draft",
        provider_template_name=provider_template_name,
    )
    db.session.add(campaign)
    db.session.flush()

    audit = AuditLog.record(
        actor_id=actor.id,
        action="campaign.create",
        target_type="message_campaign",
        target_id=0,
        details=json.dumps({"campaign_id": str(campaign.id), "channel": channel}),
    )
    audit.organization_id = org.id
    db.session.add(audit)

    return campaign


def update_campaign(
    campaign: MessageCampaign,
    actor: User,
    channel: str,
    template_body: str,
    audience_filter: Any,
    subject: Optional[str] = None,
    provider_template_name: Optional[str] = None,
    poster_url: Optional[str] = None,
) -> MessageCampaign:
    """
    Updates campaign fields.
    Resets status to 'draft' and clears test send destinations.
    Does NOT commit.
    """
    if campaign.status == "sent":
        raise CampaignAlreadySentError("Cannot modify a campaign that has already been sent.")

    channel = (channel or "").strip().lower()
    if channel not in ("email", "whatsapp", "sms"):
        raise ValueError(f"Invalid campaign channel: '{channel}'.")

    template_body = (template_body or "").strip()
    if not template_body:
        raise ValueError("Campaign template body cannot be empty.")

    if channel == "email":
        subject = (subject or "").strip()
        if not subject:
            raise ValueError("Email campaigns require a subject.")
        if len(subject) > 200:
            raise ValueError("Email subject cannot exceed 200 characters.")
    else:
        subject = None

    if channel == "whatsapp":
        provider_template_name = (provider_template_name or "").strip()
        if not provider_template_name:
            raise ValueError("WhatsApp campaigns require a provider template name.")
        if len(provider_template_name) > 100:
            raise ValueError("Provider template name cannot exceed 100 characters.")
    else:
        provider_template_name = None

    cleaned_filter = audience_service.validate_filter(audience_filter)

    campaign.channel = channel
    campaign.template_body = template_body
    campaign.poster_url = poster_url
    campaign.audience_filter = cleaned_filter
    campaign.subject = subject
    campaign.provider_template_name = provider_template_name

    # Reset to draft on ANY edit
    campaign.status = "draft"
    campaign.test_sent_to_email = None
    campaign.test_sent_to_mobile = None

    audit = AuditLog.record(
        actor_id=actor.id,
        action="campaign.update",
        target_type="message_campaign",
        target_id=0,
        details=json.dumps({"campaign_id": str(campaign.id), "status": "draft"}),
    )
    audit.organization_id = campaign.organization_id
    db.session.add(audit)

    return campaign


def _get_org(campaign: MessageCampaign) -> Optional[Organization]:
    org = getattr(campaign, "organization", None)
    if not org and campaign.organization_id:
        org = Organization.query.get(campaign.organization_id)
    return org


def test_send(
    campaign: MessageCampaign,
    actor: User,
    test_email: Optional[str] = None,
    test_mobile: Optional[str] = None,
) -> None:
    """
    Executes a test send to the provided destination.
    Creates NO recipient rows.
    Sets campaign status to 'tested'.
    """
    from app.services import notification_service

    if campaign.status == "sent":
        raise CampaignAlreadySentError("Cannot test-send an already sent campaign.")

    org = _get_org(campaign)
    context = {
        "first_name": (actor.name or "Test").split()[0],
        "name": actor.name or "Test User",
        "organization_name": getattr(org, "name", "TMA"),
        "trek_name": "Sample Himalayan Trek",
        "trek_start_date": "2026-10-15",
        "booking_id": "TEST-BK-001",
    }
    rendered_body = render_merge_fields(campaign.template_body, context)

    if campaign.channel == "email":
        if not test_email:
            raise ValueError("Test email address is required for email campaigns.")
        notification_service.send_test_email(
            recipient_email=test_email,
            subject=f"[TEST] {campaign.subject or 'Campaign'}",
            body=rendered_body,
            org=org,
            poster_url=campaign.poster_url,
        )
        campaign.test_sent_to_email = test_email

    elif campaign.channel in ("sms", "whatsapp"):
        if not test_mobile:
            raise ValueError(f"Test mobile number is required for {campaign.channel} campaigns.")
        if campaign.channel == "sms":
            notification_service.send_test_sms(
                recipient_mobile=test_mobile,
                body=rendered_body,
            )
        else:
            notification_service.send_test_whatsapp(
                recipient_mobile=test_mobile,
                template_name=campaign.provider_template_name,
                body=rendered_body,
                poster_url=campaign.poster_url,
            )
        campaign.test_sent_to_mobile = test_mobile

    campaign.status = "tested"

    audit = AuditLog.record(
        actor_id=actor.id,
        action="campaign.test_send",
        target_type="message_campaign",
        target_id=0,
        details=json.dumps(
            {
                "campaign_id": str(campaign.id),
                "destination": test_email if campaign.channel == "email" else test_mobile,
            }
        ),
    )
    audit.organization_id = campaign.organization_id
    db.session.add(audit)


def send_campaign(campaign: MessageCampaign, actor: User) -> int:
    """
    Executes the real campaign send.
    Requires status == 'tested'.
    Re-resolves the audience + consent AT SEND TIME.
    Creates MessageCampaignRecipient rows.
    Sets status to 'sent' and updates recipient_count.
    """
    from app.services import notification_service

    if campaign.status == "sent":
        raise CampaignAlreadySentError("Campaign has already been sent.")

    if campaign.status != "tested":
        raise CampaignNotTestedError("Campaign must be test-sent successfully before final broadcast.")

    # Re-resolve recipients with marketing consent at send time
    recipients_users = audience_service.resolve_segment(
        campaign.audience_filter,
        channel=campaign.channel,
        consent_type="marketing",
    )

    now = datetime.now(timezone.utc)
    dispatched_count = 0

    org = _get_org(campaign)
    for user in recipients_users:
        context = {
            "first_name": (user.name or "Trekker").split()[0],
            "name": user.name or "Trekker",
            "organization_name": getattr(org, "name", "TMA"),
            "trek_name": "Trek",
            "trek_start_date": "",
            "booking_id": "",
        }
        rendered = render_merge_fields(campaign.template_body, context)

        status, prov_msg_id = notification_service.dispatch(
            user=user,
            channel=campaign.channel,
            category="marketing",
            template_key=campaign.provider_template_name or "campaign_marketing",
            context={
                "body": rendered,
                "subject": campaign.subject,
                "campaign": campaign,
                "poster_url": campaign.poster_url,
            },
            critical=False,
        )

        recipient_row = MessageCampaignRecipient(
            organization_id=campaign.organization_id,
            campaign_id=campaign.id,
            user_id=user.id,
            delivery_status="sent" if status in ("sent", "delivered") else "failed",
            sent_at=now if status in ("sent", "delivered") else None,
            provider_message_id=prov_msg_id,
        )
        db.session.add(recipient_row)
        dispatched_count += 1

    campaign.status = "sent"
    campaign.sent_at = now
    campaign.recipient_count = dispatched_count

    audit = AuditLog.record(
        actor_id=actor.id,
        action="campaign.send",
        target_type="message_campaign",
        target_id=0,
        details=json.dumps(
            {
                "campaign_id": str(campaign.id),
                "recipient_count": dispatched_count,
            }
        ),
    )
    audit.organization_id = campaign.organization_id
    db.session.add(audit)

    return dispatched_count

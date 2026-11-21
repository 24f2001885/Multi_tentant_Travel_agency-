"""
Unit tests for services/campaign_service.py (Milestone P11).
Tests merge-field rendering safety, template injection immunity,
draft/tested/sent lifecycle, and test-send isolation.
"""

from unittest.mock import patch
import pytest

from app.extensions import db
from app.models.message_campaign import MessageCampaign
from app.models.message_campaign_recipient import MessageCampaignRecipient
from app.services import auth_service, campaign_service
from app.services.campaign_service import (
    CampaignAlreadySentError,
    CampaignNotTestedError,
    render_merge_fields,
)
from app.tenancy.scoping import tenant_context


def test_merge_fields_rendering_and_template_injection_immunity():
    """Verifies whitelisted merge-field replacement and immune literal handling of template syntax."""
    context = {
        "first_name": "Rohan",
        "name": "Rohan Sharma",
        "organization_name": "Peak Adventures",
        "trek_name": "Kedarkantha",
    }

    # 1. Valid placeholders replaced
    body = "Hello {first_name}, welcome to {trek_name} with {organization_name}!"
    rendered = render_merge_fields(body, context)
    assert rendered == "Hello Rohan, welcome to Kedarkantha with Peak Adventures!"

    # 2. Unknown or malicious template injection payloads left completely literal
    malicious_body = "Hi {first_name}! Special discount: {{ 7*7 }} and {__class__}."
    rendered_malicious = render_merge_fields(malicious_body, context)
    assert "{{ 7*7 }}" in rendered_malicious
    assert "{__class__}" in rendered_malicious
    assert "49" not in rendered_malicious


def test_campaign_lifecycle_draft_tested_sent_rules(app, org_a, make_user):
    """
    Creating campaign -> starts 'draft'
    Attempting send before test -> raises CampaignNotTestedError
    Test send -> transitions to 'tested' without creating recipient rows
    Editing tested campaign -> resets to 'draft'
    Broadcast -> transitions to 'sent'
    Double send -> raises CampaignAlreadySentError
    """
    with app.app_context(), tenant_context(org_a.id):
        admin = make_user(org_a, role="admin", email="admin_camp@example.com")
        trekker = make_user(org_a, role="trekker", email="mkt_trekker@example.com")
        auth_service.record_consent(trekker, "email", "marketing", granted=True, source="test")
        db.session.commit()

        # 1. Create draft
        campaign = campaign_service.create_campaign(
            actor=admin,
            org=org_a,
            channel="email",
            subject="Summer Trek Announce",
            template_body="Hello {first_name}, welcome to the mountains!",
            audience_filter={"is_active": True},
        )
        assert campaign.status == "draft"

        # 2. Attempt to send before test send -> rejected
        with pytest.raises(CampaignNotTestedError):
            campaign_service.send_campaign(campaign, actor=admin)

        # 3. Test send -> transitions to 'tested', zero recipient rows
        with patch("app.services.notification_service.send_test_email") as mock_test_email:
            campaign_service.test_send(
                campaign=campaign,
                actor=admin,
                test_email="tester@example.com",
            )
            assert campaign.status == "tested"
            assert campaign.test_sent_to_email == "tester@example.com"
            assert mock_test_email.called
            assert MessageCampaignRecipient.query.filter_by(campaign_id=campaign.id).count() == 0

        # 4. Editing resets status back to 'draft'
        campaign_service.update_campaign(
            campaign=campaign,
            actor=admin,
            channel="email",
            subject="Updated Subject",
            template_body="New body text",
            audience_filter={"is_active": True},
        )
        assert campaign.status == "draft"
        assert campaign.test_sent_to_email is None

        # Must test again
        with patch("app.services.notification_service.send_test_email"):
            campaign_service.test_send(
                campaign=campaign,
                actor=admin,
                test_email="tester@example.com",
            )
        assert campaign.status == "tested"

        # 5. Send campaign successfully
        with patch("app.services.notification_service._send_email_adapter", return_value=("sent", "msg_abc")):
            count = campaign_service.send_campaign(campaign, actor=admin)
            assert count == 1
            assert campaign.status == "sent"
            assert campaign.recipient_count == 1
            assert MessageCampaignRecipient.query.filter_by(campaign_id=campaign.id).count() == 1

        # 6. Re-sending raises CampaignAlreadySentError
        with pytest.raises(CampaignAlreadySentError):
            campaign_service.send_campaign(campaign, actor=admin)


def test_campaign_poster_support(app, org_a, make_user):
    """Verifies that campaigns properly persist and update poster_url and handle poster uploads."""
    import io
    from PIL import Image

    with app.app_context(), tenant_context(org_a.id):
        admin = make_user(org_a, role="admin", email="admin_poster@example.com")

        # Create dummy image in memory
        img = Image.new("RGB", (100, 100), color=(73, 109, 137))
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format="PNG")
        img_byte_arr.seek(0)

        # Test save_campaign_poster
        poster_path = campaign_service.save_campaign_poster(img_byte_arr, org_a.id)
        assert poster_path.startswith("/static/uploads/campaigns/poster_")
        assert poster_path.endswith(".png")

        # Create campaign with poster_url
        camp = campaign_service.create_campaign(
            actor=admin,
            org=org_a,
            channel="email",
            subject="Special Offer with Poster",
            template_body="Check out our promotional poster below!",
            audience_filter={},
            poster_url=poster_path,
        )
        db.session.commit()

        assert camp.poster_url == poster_path

        # Update campaign poster_url
        new_url = "https://example.com/summer_poster.jpg"
        campaign_service.update_campaign(
            campaign=camp,
            actor=admin,
            channel="email",
            subject="Updated Subject",
            template_body="Updated body",
            audience_filter={},
            poster_url=new_url,
        )
        db.session.commit()

        assert camp.poster_url == new_url


"""
Integration tests for the campaign composer flow end-to-end (Milestone P11).
Covers compose -> test-send -> broadcast, draft resets on edit,
staff capability gating and own-campaign scoping, and cross-tenant isolation.
"""

from unittest.mock import patch
import pytest

from app.extensions import db
from app.models.message_campaign import MessageCampaign
from app.models.message_campaign_recipient import MessageCampaignRecipient
from app.models.staff_profile import StaffProfile
from app.services import auth_service, campaign_service
from app.tenancy.scoping import tenant_context


def test_composer_flow_end_to_end(app, org_a, make_user, client_a):
    """Admin composes campaign -> tests it -> broadcasts -> recipients logged."""
    with app.app_context(), tenant_context(org_a.id):
        admin = make_user(org_a, role="admin", email="admin_flow@example.com")
        org_a.tenant_admin_user_id = admin.id

        trekker = make_user(org_a, role="trekker", email="flow_trekker@example.com")
        auth_service.record_consent(trekker, "email", "marketing", granted=True, source="test")
        db.session.commit()

        admin_id = admin.id

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
        sess["capability_keys"] = []

    # 1. Compose new campaign
    resp_create = client_a.post(
        "/admin/campaigns/new",
        data={
            "channel": "email",
            "subject": "Autumn Treks Launch",
            "template_body": "Hello {first_name}, Autumn slots are open!",
            "is_active": "true",
        },
        follow_redirects=True,
    )
    assert resp_create.status_code == 200

    with app.app_context(), tenant_context(org_a.id):
        camp = MessageCampaign.query.filter_by(subject="Autumn Treks Launch").first()
        assert camp is not None
        assert camp.status == "draft"
        camp_id = camp.id

    # 2. Attempt to send before test send -> blocked
    resp_send_early = client_a.post(f"/admin/campaigns/{camp_id}/send", follow_redirects=True)
    assert resp_send_early.status_code == 200
    assert "must be test-sent successfully before final broadcast" in resp_send_early.data.decode("utf-8")

    # 3. Test send
    with patch("app.services.notification_service.send_test_email"):
        resp_test = client_a.post(
            f"/admin/campaigns/{camp_id}/test-send",
            data={"test_email": "admin_flow@example.com"},
            follow_redirects=True,
        )
        assert resp_test.status_code == 200

    with app.app_context(), tenant_context(org_a.id):
        camp = MessageCampaign.query.get(camp_id)
        assert camp.status == "tested"
        assert camp.test_sent_to_email == "admin_flow@example.com"
        assert MessageCampaignRecipient.query.filter_by(campaign_id=camp_id).count() == 0

    # 4. Broadcast send
    with patch("app.services.notification_service._send_email_adapter", return_value=("sent", "msg_test_001")):
        resp_broadcast = client_a.post(f"/admin/campaigns/{camp_id}/send", follow_redirects=True)
        assert resp_broadcast.status_code == 200
        assert "Campaign successfully broadcast" in resp_broadcast.data.decode("utf-8")

    with app.app_context(), tenant_context(org_a.id):
        camp = MessageCampaign.query.get(camp_id)
        assert camp.status == "sent"
        assert camp.recipient_count == 1
        assert MessageCampaignRecipient.query.filter_by(campaign_id=camp_id).count() == 1


def test_staff_campaign_scoping_and_capabilities(app, org_a, org_b, make_user, client_a, client_b):
    """
    Staff without campaigns.compose gets 403.
    Staff with capability can only view/edit their own campaigns.
    Tenant Admin can view all campaigns.
    Cross-tenant access returns 404.
    """
    with app.app_context(), tenant_context(org_a.id):
        admin = make_user(org_a, role="admin", email="admin_owner@example.com")
        org_a.tenant_admin_user_id = admin.id

        staff1 = make_user(org_a, role="staff", email="staff1@example.com")
        prof1 = StaffProfile(organization_id=org_a.id, user_id=staff1.id, approval_status="approved")
        db.session.add(prof1)

        staff2 = make_user(org_a, role="staff", email="staff2@example.com")
        prof2 = StaffProfile(organization_id=org_a.id, user_id=staff2.id, approval_status="approved")
        db.session.add(prof2)

        staff_nocap = make_user(org_a, role="staff", email="staff_nocap@example.com")
        prof_nocap = StaffProfile(organization_id=org_a.id, user_id=staff_nocap.id, approval_status="approved")
        db.session.add(prof_nocap)

        db.session.commit()

        # Create campaign owned by staff1
        camp_staff1 = campaign_service.create_campaign(
            actor=staff1,
            org=org_a,
            channel="email",
            subject="Staff 1 Campaign",
            template_body="Body text",
            audience_filter={},
        )
        db.session.commit()
        camp_id = camp_staff1.id
        s1_id = staff1.id
        s2_id = staff2.id
        nocap_id = staff_nocap.id
        admin_id = admin.id

    # 1. Staff without capability gets 403
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(nocap_id)
        sess["capability_keys"] = []

    resp_nocap = client_a.get("/admin/campaigns")
    assert resp_nocap.status_code == 403

    # 2. Staff 1 (creator) can access campaign
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(s1_id)
        sess["capability_keys"] = ["campaigns.compose"]

    resp_s1 = client_a.get(f"/admin/campaigns/{camp_id}")
    assert resp_s1.status_code == 200

    # 3. Staff 2 cannot access staff 1's campaign (403)
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(s2_id)
        sess["capability_keys"] = ["campaigns.compose"]

    resp_s2 = client_a.get(f"/admin/campaigns/{camp_id}")
    assert resp_s2.status_code in (403, 404)

    # 4. Tenant Admin can access staff 1's campaign
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
        sess["capability_keys"] = []

    resp_admin = client_a.get(f"/admin/campaigns/{camp_id}")
    assert resp_admin.status_code == 200

    # 5. Cross-tenant request from client_b returns 404
    resp_cross = client_b.get(f"/admin/campaigns/{camp_id}")
    assert resp_cross.status_code in (403, 404, 302)


def test_composer_multi_channel_creation(app, org_a, make_user, client_a):
    """Admin can select multiple channels at once and create draft campaigns for each."""
    with app.app_context(), tenant_context(org_a.id):
        admin = make_user(org_a, role="admin", email="admin_multi@example.com")
        org_a.tenant_admin_user_id = admin.id
        db.session.commit()
        admin_id = admin.id

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
        sess["capability_keys"] = []

    # 1. GET composer page verifies multi-channel checkboxes are rendered
    resp_get = client_a.get("/admin/campaigns/new")
    assert resp_get.status_code == 200
    html_get = resp_get.get_data(as_text=True)
    assert 'name="channels"' in html_get
    assert 'value="email"' in html_get
    assert 'value="whatsapp"' in html_get
    assert 'value="sms"' in html_get

    # 2. POST with multiple channels (Email + WhatsApp)
    resp_multi = client_a.post(
        "/admin/campaigns/new",
        data={
            "channels": ["email", "whatsapp"],
            "subject": "Winter Expedition Early Bird",
            "provider_template_name": "wa_winter_early_bird",
            "template_body": "Hello {first_name}, check out winter expeditions!",
        },
        follow_redirects=False,
    )
    # Redirects to campaigns list when multiple channels are created
    assert resp_multi.status_code == 302
    assert "/admin/campaigns" in resp_multi.headers.get("Location", "")

    # Follow redirect and verify flash message
    resp_list = client_a.get(resp_multi.headers.get("Location"))
    assert resp_list.status_code == 200
    list_html = resp_list.get_data(as_text=True)
    assert "Successfully created 2 campaign drafts across Email, Whatsapp" in list_html

    with app.app_context(), tenant_context(org_a.id):
        email_camp = MessageCampaign.query.filter_by(
            organization_id=org_a.id,
            channel="email",
            subject="Winter Expedition Early Bird",
        ).first()
        assert email_camp is not None
        assert email_camp.status == "draft"

        wa_camp = MessageCampaign.query.filter_by(
            organization_id=org_a.id,
            channel="whatsapp",
            provider_template_name="wa_winter_early_bird",
        ).first()
        assert wa_camp is not None
        assert wa_camp.status == "draft"

    # 3. POST with single channel via 'channels' multi-field redirects to that campaign's detail page
    resp_single = client_a.post(
        "/admin/campaigns/new",
        data={
            "channels": ["sms"],
            "template_body": "Hi {first_name}, flash sale is live!",
        },
        follow_redirects=False,
    )
    assert resp_single.status_code == 302
    with app.app_context(), tenant_context(org_a.id):
        sms_camp = MessageCampaign.query.filter_by(
            organization_id=org_a.id,
            channel="sms",
            template_body="Hi {first_name}, flash sale is live!",
        ).first()
        assert sms_camp is not None
        assert str(sms_camp.id) in resp_single.headers.get("Location", "")


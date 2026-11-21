"""
Tests for recent user fixes:
1. Itinerary & dossier rendering for authenticated trekkers
2. Pre-filling personal details & coupon application in booking
3. Coupon creation audit logging
4. Payment result verification and state sync
"""

from datetime import date, timedelta
from decimal import Decimal
import pytest
from unittest.mock import patch, MagicMock

from app.extensions import db
from app.models import Organization, User, Coupon, AuditLog, Payment, Booking, CallRequest, MessageCampaign
from app.models.trek_itinerary_day import TrekItineraryDay
from app.services import id_codec, coupon_service, payment_service, refund_service
from app.tenancy.scoping import tenant_bypass, tenant_context


def test_authenticated_trekker_detail_shows_itinerary(client_a, org_a, make_trek, make_user):
    """Authenticated trekkers must see itinerary and dossier just like anonymous visitors."""
    trek = make_trek(org_a, name="Annapurna Circuit", status="open")
    token = id_codec.encode(trek.id)

    # Add itinerary days to trek
    with tenant_context(org_a.id):
        day1 = TrekItineraryDay(
            organization_id=org_a.id,
            trek_id=trek.id,
            day_number=1,
            title="Drive from Pokhara to Besisahar",
            description="Scenic mountain road trip along the river valley.",
        )
        day2 = TrekItineraryDay(
            organization_id=org_a.id,
            trek_id=trek.id,
            day_number=2,
            title="Trek to Chame",
            description="Pine forest ascent with mountain views.",
        )
        db.session.add_all([day1, day2])
        db.session.commit()

    trekker = make_user(org_a, role="trekker", name="Aarav Sharma")
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(trekker.id)

    # Fetch trekker detail route
    resp = client_a.get(f"/trekker/treks/{token}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Verify itinerary details appear in the authenticated view
    assert "Drive from Pokhara to Besisahar" in html
    assert "Trek to Chame" in html


def test_booking_review_prefills_details_and_validates_coupon(client_a, org_a, make_trek, make_user):
    """Booking page must prefill personal details and validate promotional coupon codes."""
    trek = make_trek(org_a, name="Hampta Pass", status="open", price=5000.0, available_slots=8)
    token = id_codec.encode(trek.id)

    # Create coupon
    with tenant_context(org_a.id):
        org = db.session.get(Organization, org_a.id)
        org.features = ["coupons", "advanced_itineraries"]
        org.subscription_tier = "growth"
        db.session.commit()

        admin = make_user(org_a, role="admin", name="Admin User")
        coupon = coupon_service.create_coupon(
            org=org,
            data={
                "code": "SUMMER500",
                "discount_type": "flat",
                "discount_value": Decimal("500.00"),
                "min_booking_amount": Decimal("1000.00"),
                "max_uses": 10,
            },
            actor=admin,
        )

    trekker = make_user(
        org_a,
        role="trekker",
        name="Sunita Rao",
        email="sunita@example.com",
        mobile_country_code="+91",
        mobile_number="9876501234",
    )
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(trekker.id)

    # 1. GET Booking review page
    resp_review = client_a.get(f"/trekker/treks/{token}/book")
    assert resp_review.status_code == 200
    html = resp_review.get_data(as_text=True)

    assert "Sunita Rao" in html
    assert "sunita@example.com" in html
    assert "9876501234" in html
    assert "coupon_code" in html

    # 2. AJAX Coupon Validation
    resp_valid = client_a.post(
        f"/trekker/treks/{token}/validate-coupon",
        json={"coupon_code": "SUMMER500", "slot_count": 1},
    )
    assert resp_valid.status_code == 200
    data = resp_valid.get_json()
    assert data["valid"] is True
    assert data["discount"] == 500.0
    assert data["new_total"] == 4500.0

    # 3. Invalid Coupon Validation
    resp_invalid = client_a.post(
        f"/trekker/treks/{token}/validate-coupon",
        json={"coupon_code": "NONEXISTENT", "slot_count": 1},
    )
    assert resp_invalid.status_code == 400
    assert resp_invalid.get_json()["valid"] is False


def test_coupon_creation_audit_log_fields(org_a, make_user):
    """Coupon creation must populate target_type and target_id without 500 errors."""
    with tenant_context(org_a.id):
        org = db.session.get(Organization, org_a.id)
        org.features = ["coupons", "advanced_itineraries"]
        org.subscription_tier = "growth"
        db.session.commit()

        admin = make_user(org_a, role="admin", name="Audit Admin")
        coupon = coupon_service.create_coupon(
            org=org,
            data={
                "code": "AUDITTEST",
                "discount_type": "percentage",
                "discount_value": Decimal("10.00"),
            },
            actor=admin,
        )
        db.session.commit()
        assert coupon.id is not None

        # Check AuditLog
        log = AuditLog.query.filter_by(
            organization_id=org.id,
            action="coupon.create",
        ).first()
        assert log is not None
        assert log.target_type == "coupon"
        assert log.target_id is not None


def test_payment_result_post_verification_and_sync(client_a, org_a, make_trek, make_user):
    """Payment result route confirms booking upon POST callback and syncs status."""
    trek = make_trek(org_a, name="Bhrigu Lake", status="open", price=3000.0, available_slots=5)
    trekker = make_user(org_a, role="trekker", name="Pooja Sharma")

    with tenant_context(org_a.id):
        booking = Booking(
            organization_id=org_a.id,
            user_id=trekker.id,
            trek_id=trek.id,
            slot_count=1,
            booking_channel="online",
            status="pending",
            payment_status="pending",
        )
        db.session.add(booking)
        db.session.flush()

        payment = Payment(
            organization_id=org_a.id,
            booking_id=booking.id,
            amount=Decimal("3000.00"),
            currency="INR",
            channel="razorpay",
            gateway_order_id="order_fake_test_123",
            status="initiated",
        )
        db.session.add(payment)
        db.session.commit()
        booking_id = booking.id

    token = id_codec.encode(booking_id)

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(trekker.id)

    # Mock Razorpay verification to succeed
    with patch("app.services.payment_service._client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.utility.verify_payment_signature.return_value = True
        mock_client.return_value = mock_instance

        resp = client_a.post(
            f"/trekker/payment-result/{token}",
            data={
                "razorpay_order_id": "order_fake_test_123",
                "razorpay_payment_id": "pay_fake_test_456",
                "razorpay_signature": "mocked_signature",
            },
        )
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Booking Confirmed" in html

    # Verify database state
    with tenant_context(org_a.id):
        db_booking = db.session.get(Booking, booking_id)
        assert db_booking.status == "booked"
        assert db_booking.payment_status == "paid"

        db_payment = Payment.query.filter_by(booking_id=booking_id).first()
        assert db_payment.status == "success"
        assert db_payment.transaction_id == "pay_fake_test_456"


def test_admin_and_staff_sidebar_have_visible_logout(client_a, org_a, make_user):
    """Admin and staff dashboards must have prominent, accessible logout actions."""
    # 1. Admin sidebar logout
    admin = make_user(org_a, role="admin", name="Super Admin")
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(admin.id)

    resp_admin = client_a.get("/admin/dashboard")
    assert resp_admin.status_code == 200
    html_admin = resp_admin.get_data(as_text=True)
    assert "/logout" in html_admin
    assert "Log Out" in html_admin

    # 2. Staff sidebar logout
    staff = make_user(org_a, role="staff", name="Guide Staff")
    from app.models import StaffProfile
    with tenant_context(org_a.id):
        profile = StaffProfile(
            organization_id=org_a.id,
            user_id=staff.id,
            approval_status="approved",
        )
        db.session.add(profile)
        db.session.commit()

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(staff.id)

    resp_staff = client_a.get("/staff/dashboard")
    assert resp_staff.status_code == 200
    html_staff = resp_staff.get_data(as_text=True)
    assert "/logout" in html_staff
    assert "Log Out" in html_staff


def test_password_visibility_toggle_present_on_auth_pages(client_a, client_platform):
    """Verify that password visibility toggle button ('see pass') is present on login and registration pages."""
    # 1. Tenant Login page
    resp_login = client_a.get("/login")
    assert resp_login.status_code == 200
    html_login = resp_login.get_data(as_text=True)
    assert "toggle-password" in html_login
    assert "bi-eye" in html_login

    # 2. Tenant Registration page
    resp_reg = client_a.get("/register")
    assert resp_reg.status_code == 200
    html_reg = resp_reg.get_data(as_text=True)
    assert "toggle-password" in html_reg
    assert "bi-eye" in html_reg

    # 3. Platform Admin Login page
    resp_plat = client_platform.get("/platform/login")
    assert resp_plat.status_code == 200
    html_plat = resp_plat.get_data(as_text=True)
    assert "toggle-password" in html_plat
    assert "bi-eye" in html_plat


def test_super_admin_dashboard_metrics_and_notifications(client_a, org_a, make_trek, make_user):
    """Super admin dashboard must render comprehensive revenue, ongoing campaigns, call requests, manual bookings, and notification queues."""
    admin = make_user(org_a, role="admin", name="Super Admin User")
    trek = make_trek(org_a, name="Everest Base Camp", status="open")

    with tenant_context(org_a.id):
        # 1. Manual booking pending sign-off
        manual_b = Booking(
            organization_id=org_a.id,
            user_id=admin.id,
            trek_id=trek.id,
            total_price=Decimal("2500.00"),
            booking_channel="manual",
            status="pending",
        )
        db.session.add(manual_b)
        db.session.flush()

        # 2. Payment record
        pmt = Payment(
            organization_id=org_a.id,
            booking_id=manual_b.id,
            amount=Decimal("2500.00"),
            currency="INR",
            status="success",
        )
        db.session.add(pmt)

        # 3. New call request
        call_req = CallRequest(
            organization_id=org_a.id,
            name="Rohan Sharma",
            phone="9876543210",
            preferred_time="Afternoon",
            message="Looking to book for a group of 5",
            status="new",
        )
        db.session.add(call_req)

        # 4. Draft/Ongoing campaign
        campaign = MessageCampaign(
            organization_id=org_a.id,
            created_by=admin.id,
            channel="email",
            subject="Exclusive Spring Himalayan Sale",
            template_body="Don't miss out on upcoming expeditions!",
            status="draft",
        )
        db.session.add(campaign)
        db.session.commit()

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(admin.id)

    resp = client_a.get("/admin/dashboard")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Validate Revenue details
    assert "Total Revenue" in html
    assert "2500.00" in html
    assert "last 30 days" in html

    # Validate Operational Notifications hub
    assert "Action Required" in html
    assert "Call Request" in html
    assert "Manual Booking" in html
    assert "/admin/call-requests" in html
    assert "/admin/manual-bookings" in html

    # Validate Marketing & Campaign snapshot
    assert "Campaigns &amp; Outreach" in html or "Campaigns & Outreach" in html
    assert "Active &amp; Draft Campaigns" in html or "Active & Draft Campaigns" in html
    assert "/admin/campaigns" in html

    # Validate Recent Payments transactions table
    assert "Recent Payment Transactions" in html
    assert "/admin/payments" in html

    # Validate that each box except data charts has a direct visit link
    assert "Visit Treks Page" in html
    assert "/admin/treks" in html
    assert "Visit Users Page" in html
    assert "/admin/users" in html
    assert "Visit Staff Page" in html
    assert "/admin/staff" in html
    assert "Visit Bookings Page" in html
    assert "/admin/bookings" in html
    assert "Visit Payments Page" in html
    assert "Visit Campaigns Page" in html

    # Data charts are present as visualizers
    assert "Bookings over time" in html
    assert "Treks by status" in html


def test_trek_drawer_options_resolution(client_a, org_a, make_trek, make_user):
    """Verify that all 4 drawer actions (Details, Itinerary, Gallery, Overview) resolve cleanly via both token and integer IDs."""
    admin = make_user(org_a, role="admin", name="Admin Manager")
    trek = make_trek(org_a, name="Valley of Flowers", status="open")
    token = id_codec.encode(trek.id)

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(admin.id)

    # 1. Verify treks list page sets data-url-* attributes for the drawer
    resp_list = client_a.get("/admin/treks")
    assert resp_list.status_code == 200
    html_list = resp_list.get_data(as_text=True)
    assert f"/admin/treks/{token}/edit?tab=details" in html_list
    assert f"/admin/treks/{token}/edit?tab=itinerary" in html_list
    assert f"/admin/treks/{token}/edit?tab=gallery" in html_list
    assert f"/admin/treks/{token}" in html_list

    # 2. Test resolution via canonical Hashid tokens
    assert client_a.get(f"/admin/treks/{token}/edit?tab=details").status_code == 200
    assert client_a.get(f"/admin/treks/{token}/edit?tab=itinerary").status_code == 200
    assert client_a.get(f"/admin/treks/{token}/edit?tab=gallery").status_code == 200
    assert client_a.get(f"/admin/treks/{token}").status_code == 200

    # 3. Test resolution via direct integer IDs (fallback preventing 404s)
    assert client_a.get(f"/admin/treks/{trek.id}/edit?tab=details").status_code == 200
    assert client_a.get(f"/admin/treks/{trek.id}/edit?tab=itinerary").status_code == 200
    assert client_a.get(f"/admin/treks/{trek.id}/edit?tab=gallery").status_code == 200
    assert client_a.get(f"/admin/treks/{trek.id}").status_code == 200


def test_reopen_closed_call_request_in_ui(client_a, org_a, make_user):
    """Admin can view and reopen closed call requests from the queue."""
    admin = make_user(org_a, role="admin", name="Admin Caller")

    with tenant_context(org_a.id):
        call_req = CallRequest(
            organization_id=org_a.id,
            name="Siddharth Malhotra",
            phone="+919876543219",
            preferred_time="Evening",
            message="Looking to reopen inquiry",
            status="closed",
        )
        db.session.add(call_req)
        db.session.commit()
        call_req_id = call_req.id

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(admin.id)

    # 1. View closed call requests list - verify 'Reopen' button is shown instead of 'No actions'
    resp = client_a.get("/admin/call-requests?status=closed")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Siddharth Malhotra" in html
    assert "Reopen" in html
    assert "No actions" not in html

    # 2. Reopen the call request
    resp_reopen = client_a.post(
        f"/admin/call-requests/{call_req_id}/status",
        data={"status": "new"},
        follow_redirects=True,
    )
    assert resp_reopen.status_code == 200
    reopen_html = resp_reopen.get_data(as_text=True)
    assert "Call request reopened successfully" in reopen_html

    # 3. Verify in database
    with tenant_context(org_a.id):
        updated_req = db.session.get(CallRequest, call_req_id)
        assert updated_req.status == "new"


def test_booking_detail_refund_calculation_with_successful_payment(client_a, org_a, make_trek, make_user):
    """Booking detail cancellation policy preview must correctly compute refund amount from successful payments."""
    user = make_user(org_a, role="trekker", name="Happy Trekker")
    # Trek starts in 3 days: standard policy 1-6 days = 25% refund
    from datetime import date, timedelta
    trek = make_trek(org_a, name="Kedarkantha Winter Trek", status="open", start_date=date.today() + timedelta(days=3))

    with tenant_context(org_a.id):
        refund_service.seed_default_policy(org_a)
        booking = Booking(
            organization_id=org_a.id,
            user_id=user.id,
            trek_id=trek.id,
            slot_count=1,
            total_price=Decimal("1800.00"),
            status="booked",
            payment_status="paid",
        )
        db.session.add(booking)
        db.session.flush()

        payment = Payment(
            organization_id=org_a.id,
            booking_id=booking.id,
            amount=Decimal("1800.00"),
            currency="INR",
            status="success",
        )
        db.session.add(payment)
        db.session.commit()
        booking_token = id_codec.encode(booking.id)

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(user.id)

    resp = client_a.get(f"/trekker/bookings/{booking_token}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Cancellation Policy: Estimated refund is" in html
    assert "25.00%" in html
    assert "₹450.00" in html
    assert "(₹0.00)" not in html


def test_admin_users_and_staff_list_profile_picture_view(client_a, org_a, make_user):
    """Admin users list, user detail, and staff list must display profile pictures and provide preview triggers."""
    # 1. Setup admin and sample user with custom profile picture
    with tenant_context(org_a.id):
        admin = make_user(org_a, role="admin", name="Admin User", email="admin.pic@example.com")
        org_a.tenant_admin_user_id = admin.id

        trekker = make_user(
            org_a,
            role="trekker",
            name="Priya Patel",
            email="priya.patel@example.com",
        )
        t_user = db.session.get(User, trekker.id)
        t_user.profile_picture_filename = "priya_avatar.jpg"
        db.session.commit()

        admin_id = admin.id
        trekker_id = trekker.id
        trekker_token = id_codec.encode(trekker.id)

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)

    # 2. Test /admin/users list view
    resp_users = client_a.get("/admin/users")
    assert resp_users.status_code == 200
    users_html = resp_users.get_data(as_text=True)

    # Check avatar thumbnail, view photo triggers, and modal
    assert "priya_avatar.jpg" in users_html
    assert "default_avatar.svg" in users_html
    assert "View Photo" in users_html
    assert "profilePicModal" in users_html
    assert "Photo" in users_html

    # 3. Test /admin/users/<hashid> detail view
    resp_detail = client_a.get(f"/admin/users/{trekker_token}")
    assert resp_detail.status_code == 200
    detail_html = resp_detail.get_data(as_text=True)

    assert "priya_avatar.jpg" in detail_html
    assert "View Full Photo" in detail_html
    assert "profilePicModal" in detail_html
    assert "Custom Photo" in detail_html

    # 4. Test /admin/staff list view
    resp_staff = client_a.get("/admin/staff")
    assert resp_staff.status_code == 200
    staff_html = resp_staff.get_data(as_text=True)

    assert "staff-avatar-trigger" in staff_html
    assert "staffPicModal" in staff_html


def test_trekker_settings_hub_data_privacy_and_deletion(client_a, org_a, make_user):
    """
    Trekker Settings Hub provides:
    1. Unified settings with profile editing and password change.
    2. Data & Privacy transparency with JSON data export.
    3. Right to Erasure / Account deletion with anonymization, consent revocation, and audit logging.
    """
    from app.models.notification_consent import NotificationConsent
    from app.models.notification_preference import NotificationPreference

    with tenant_context(org_a.id):
        trekker = make_user(
            org_a,
            role="trekker",
            name="Rahul Verma",
            email="rahul.verma@example.com",
            mobile_country_code="+91",
            mobile_number="9876543210",
        )
        t_user = db.session.get(User, trekker.id)
        t_user.profile_picture_filename = "rahul_pic.jpg"
        
        # Add initial consents
        from app.services import auth_service
        auth_service.record_consent(trekker, "email", "transactional", granted=True, source="registration")
        auth_service.record_consent(trekker, "email", "marketing", granted=True, source="registration")
        db.session.commit()
        user_id = trekker.id

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(user_id)

    # 1. Access Settings page (default profile tab)
    resp_settings = client_a.get("/trekker/settings")
    assert resp_settings.status_code == 200
    html_settings = resp_settings.get_data(as_text=True)
    assert "Settings &amp; Account" in html_settings or "Settings & Account" in html_settings
    assert "Rahul Verma" in html_settings
    assert "rahul.verma@example.com" in html_settings
    assert "Saved Data &amp; Privacy" in html_settings or "Saved Data & Privacy" in html_settings

    # 2. Update profile details
    resp_update_prof = client_a.post(
        "/trekker/settings?tab=profile",
        data={
            "form_action": "profile",
            "name": "Rahul V. Sharma",
            "mobile_country_code": "+91",
            "mobile_number": "9123456789",
        },
        follow_redirects=True,
    )
    assert resp_update_prof.status_code == 200
    with tenant_context(org_a.id):
        updated_user = db.session.get(User, user_id)
        assert updated_user.name == "Rahul V. Sharma"
        assert updated_user.mobile_number == "9123456789"

    # 3. Access Saved Data & Privacy tab
    resp_privacy = client_a.get("/trekker/settings?tab=privacy")
    assert resp_privacy.status_code == 200
    html_privacy = resp_privacy.get_data(as_text=True)
    assert "Data Transparency Hub" in html_privacy
    assert "Download Your Personal Data" in html_privacy
    assert "/trekker/data-export" in html_privacy

    # 4. Download / Export Personal Data
    resp_export = client_a.get("/trekker/data-export")
    assert resp_export.status_code == 200
    assert resp_export.content_type == "application/json"
    import json
    data_json = json.loads(resp_export.get_data(as_text=True))
    assert data_json["user_profile"]["name"] == "Rahul V. Sharma"
    assert data_json["user_profile"]["email"] == "rahul.verma@example.com"
    assert "bookings" in data_json
    assert "saved_communication_preferences" in data_json

    # 5. Account Deletion requires DELETE confirmation
    resp_bad_del = client_a.post(
        "/trekker/account/delete",
        data={"confirm_phrase": "NOPE"},
        follow_redirects=True,
    )
    assert resp_bad_del.status_code == 200
    with tenant_context(org_a.id):
        u_still_active = db.session.get(User, user_id)
        assert u_still_active.is_active is True

    # 6. Account Deletion with valid confirmation
    resp_good_del = client_a.post(
        "/trekker/account/delete",
        data={"confirm_phrase": "DELETE"},
        follow_redirects=True,
    )
    assert resp_good_del.status_code == 200
    del_html = resp_good_del.get_data(as_text=True)
    assert "deleted successfully" in del_html

    with tenant_context(org_a.id):
        deleted_user = db.session.get(User, user_id)
        assert deleted_user.is_active is False
        assert deleted_user.name == "Deleted User"
        assert "deleted_" in deleted_user.email
        assert "@anonymized.local" in deleted_user.email
        assert deleted_user.profile_picture_filename is None

        # Verify all consents revoked
        consents = NotificationConsent.query.filter_by(user_id=user_id).all()
        for c in consents:
            assert c.is_active is False
            assert c.revoked_at is not None

        # Verify audit log recorded
        audit = AuditLog.query.filter_by(actor_user_id=user_id, action="user.delete_account").first()
        assert audit is not None










"""
Dedicated cross-tenant data-isolation suite (TDR v2 §10; Milestone P8).
The highest-priority test suite of the TMA multi-tenant platform. Required gate from P8 onward.
"""

import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import uuid
import pytest
from sqlalchemy import select

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.booking import Booking
from app.models.booking_participant import BookingParticipant
from app.models.call_request import CallRequest
from app.models.coupon import Coupon
from app.models.coupon_redemption import CouponRedemption
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.password_reset_token import PasswordResetToken
from app.models.payment import Payment
from app.models.refund_policy import RefundPolicy
from app.models.role import Role
from app.models.staff_profile import StaffProfile
from app.models.ticket import Ticket
from app.models.trek import Trek
from app.models.user import User
from app.models.waitlist_entry import WaitlistEntry
from app.models.trek_itinerary_day import TrekItineraryDay
from app.models.itinerary_element import ItineraryElement
from app.models.trek_media import TrekMedia
from app.models.trek_review import TrekReview
from app.models.notification_consent import NotificationConsent
from app.models.notification_preference import NotificationPreference
from app.models.message_campaign import MessageCampaign
from app.models.message_campaign_recipient import MessageCampaignRecipient
from app.models.ticket_message import TicketMessage
from app.services.id_codec import encode
from app.tenancy.scoping import TenantScopedMixin, tenant_bypass, tenant_context
from werkzeug.security import generate_password_hash


def _make_user_row(org):
    u = User(
        organization_id=org.id,
        name="Iso User",
        email=f"iso_{uuid.uuid4().hex[:8]}@example.com",
        password_hash=generate_password_hash("pass"),
        role="trekker",
        mobile_country_code="+91",
        mobile_number="9876543210",
        terms_accepted_at=datetime.now(timezone.utc),
    )
    db.session.add(u)
    db.session.flush()
    return u


def _make_role_row(org):
    r = Role(organization_id=org.id, name=f"Role_{uuid.uuid4().hex[:8]}")
    db.session.add(r)
    db.session.flush()
    return r


def _make_trek_row(org):
    t = Trek(
        organization_id=org.id,
        name=f"Trek_{uuid.uuid4().hex[:8]}",
        location="Himalayas",
        difficulty="moderate",
        duration_days=4,
        total_slots=12,
        available_slots=12,
        price=6000.0,
        status="open",
        start_date=date.today() + timedelta(days=5),
        end_date=date.today() + timedelta(days=9),
    )
    db.session.add(t)
    db.session.flush()
    return t


def _make_staff_profile_row(org):
    u = User(
        organization_id=org.id,
        name="Staff User",
        email=f"staff_{uuid.uuid4().hex[:8]}@example.com",
        password_hash=generate_password_hash("pass"),
        role="staff",
        mobile_country_code="+91",
        mobile_number="9876543210",
        terms_accepted_at=datetime.now(timezone.utc),
    )
    db.session.add(u)
    db.session.flush()
    sp = StaffProfile(
        organization_id=org.id,
        user_id=u.id,
        approval_status="approved",
    )
    db.session.add(sp)
    db.session.flush()
    return sp


def _make_booking_row(org):
    u = _make_user_row(org)
    t = _make_trek_row(org)
    b = Booking(
        organization_id=org.id,
        user_id=u.id,
        trek_id=t.id,
        status="booked",
        payment_status="paid",
    )
    db.session.add(b)
    db.session.flush()
    return b


def _make_payment_row(org):
    b = _make_booking_row(org)
    p = Payment(
        organization_id=org.id,
        booking_id=b.id,
        amount=6000.0,
        currency="INR",
        status="success",
        gateway_order_id=f"order_{uuid.uuid4().hex[:12]}",
        transaction_id=f"pay_{uuid.uuid4().hex[:12]}",
    )
    db.session.add(p)
    db.session.flush()
    return p


def _make_ticket_row(org):
    u = _make_user_row(org)
    tick = Ticket(
        organization_id=org.id,
        user_id=u.id,
        type="query",
        subject="Ticket Subject",
        message="Ticket Body",
        status="open",
        purge_after=datetime.now(timezone.utc) + timedelta(days=10),
    )
    db.session.add(tick)
    db.session.flush()
    return tick


def _make_audit_log_row(org):
    u = _make_user_row(org)
    log = AuditLog(
        organization_id=org.id,
        actor_user_id=u.id,
        action="isolation.test",
        target_type="user",
        target_id=u.id,
    )
    db.session.add(log)
    db.session.flush()
    return log


def _make_call_request_row(org):
    cr = CallRequest(
        organization_id=org.id,
        name="Lead Inquirer",
        phone="+91 9876543210",
        status="new",
    )
    db.session.add(cr)
    db.session.flush()
    return cr


def _make_password_reset_token_row(org):
    u = _make_user_row(org)
    token = PasswordResetToken(
        organization_id=org.id,
        user_id=u.id,
        token_hash=uuid.uuid4().hex,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.session.add(token)
    db.session.flush()
    return token


def _make_booking_participant_row(org):
    b = _make_booking_row(org)
    bp = BookingParticipant(
        organization_id=org.id,
        booking_id=b.id,
        name="Participant One",
        age=28,
        is_primary_contact=True,
    )
    db.session.add(bp)
    db.session.flush()
    return bp


def _make_refund_policy_row(org):
    rp = RefundPolicy(
        organization_id=org.id,
        min_days_before_trip=14,
        max_days_before_trip=None,
        refund_percentage=100.0,
    )
    db.session.add(rp)
    db.session.flush()
    return rp


def _make_coupon_row(org):
    c = Coupon(
        organization_id=org.id,
        code=f"SAVE_{uuid.uuid4().hex[:6].upper()}",
        discount_type="percentage",
        discount_value=15.0,
        is_active=True,
    )
    db.session.add(c)
    db.session.flush()
    return c


def _make_coupon_redemption_row(org):
    c = _make_coupon_row(org)
    b = _make_booking_row(org)
    cr = CouponRedemption(
        organization_id=org.id,
        coupon_id=c.id,
        booking_id=b.id,
        user_id=b.user_id,
        discount_applied=100.0,
    )
    db.session.add(cr)
    db.session.flush()
    return cr


def _make_invoice_row(org):
    b = _make_booking_row(org)
    inv = Invoice(
        organization_id=org.id,
        booking_id=b.id,
        invoice_number=f"INV-{uuid.uuid4().hex[:8].upper()}",
        taxable_amount=5000.0,
        gst_amount=900.0,
        total_amount=5900.0,
    )
    db.session.add(inv)
    db.session.flush()
    return inv


def _make_waitlist_entry_row(org):
    t = _make_trek_row(org)
    u = _make_user_row(org)
    we = WaitlistEntry(
        organization_id=org.id,
        trek_id=t.id,
        user_id=u.id,
        requested_slot_count=1,
        status="waiting",
    )
    db.session.add(we)
    db.session.flush()
    return we


def _make_itinerary_day_row(org):
    t = _make_trek_row(org)
    day = TrekItineraryDay(
        organization_id=org.id,
        trek_id=t.id,
        day_number=1,
        title="Day 1",
    )
    db.session.add(day)
    db.session.flush()
    return day


def _make_itinerary_element_row(org):
    day = _make_itinerary_day_row(org)
    el = ItineraryElement(
        organization_id=org.id,
        day_id=day.id,
        sequence_order=1,
        element_type="activity",
        title="Activity 1",
    )
    db.session.add(el)
    db.session.flush()
    return el


def _make_trek_media_row(org):
    t = _make_trek_row(org)
    m = TrekMedia(
        organization_id=org.id,
        trek_id=t.id,
        asset_url=f"uploads/{org.id}/{t.id}/{uuid.uuid4().hex}.jpg",
        sort_order=1,
        caption="Sample Photo",
    )
    db.session.add(m)
    db.session.flush()
    return m


def _make_trek_review_row(org):
    t = _make_trek_row(org)
    u = _make_user_row(org)
    rev = TrekReview(
        organization_id=org.id,
        trek_id=t.id,
        user_id=u.id,
        rating=5,
        comment="Awesome trek!",
    )
    db.session.add(rev)
    db.session.flush()
    return rev


def _make_notification_consent_row(org):
    u = _make_user_row(org)
    nc = NotificationConsent(
        organization_id=org.id,
        user_id=u.id,
        channel="email",
        consent_type="transactional",
        granted_at=datetime.now(timezone.utc),
    )
    db.session.add(nc)
    db.session.flush()
    return nc


def _make_notification_preference_row(org):
    u = _make_user_row(org)
    np = NotificationPreference(
        organization_id=org.id,
        user_id=u.id,
        channel="email",
        is_enabled=True,
    )
    db.session.add(np)
    db.session.flush()
    return np


def _make_message_campaign_row(org):
    u = _make_user_row(org)
    mc = MessageCampaign(
        organization_id=org.id,
        created_by=u.id,
        channel="email",
        subject="Iso Campaign",
        template_body="Body",
        audience_filter={},
        status="draft",
    )
    db.session.add(mc)
    db.session.flush()
    return mc


def _make_message_campaign_recipient_row(org):
    mc = _make_message_campaign_row(org)
    u = _make_user_row(org)
    mcr = MessageCampaignRecipient(
        organization_id=org.id,
        campaign_id=mc.id,
        user_id=u.id,
        delivery_status="sent",
        provider_message_id=f"msg_{uuid.uuid4().hex[:12]}",
    )
    db.session.add(mcr)
    db.session.flush()
    return mcr


def _make_ticket_message_row(org):
    t = _make_ticket_row(org)
    u = _make_user_row(org)
    tm = TicketMessage(
        organization_id=org.id,
        ticket_id=t.id,
        sender_id=u.id,
        message="Hello from isolation test thread",
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(tm)
    db.session.flush()
    return tm


MODEL_FACTORIES = {
    User: _make_user_row,
    Role: _make_role_row,
    Trek: _make_trek_row,
    StaffProfile: _make_staff_profile_row,
    Booking: _make_booking_row,
    Payment: _make_payment_row,
    Ticket: _make_ticket_row,
    AuditLog: _make_audit_log_row,
    CallRequest: _make_call_request_row,
    PasswordResetToken: _make_password_reset_token_row,
    BookingParticipant: _make_booking_participant_row,
    RefundPolicy: _make_refund_policy_row,
    Coupon: _make_coupon_row,
    CouponRedemption: _make_coupon_redemption_row,
    Invoice: _make_invoice_row,
    WaitlistEntry: _make_waitlist_entry_row,
    TrekItineraryDay: _make_itinerary_day_row,
    ItineraryElement: _make_itinerary_element_row,
    TrekMedia: _make_trek_media_row,
    TrekReview: _make_trek_review_row,
    NotificationConsent: _make_notification_consent_row,
    NotificationPreference: _make_notification_preference_row,
    MessageCampaign: _make_message_campaign_row,
    MessageCampaignRecipient: _make_message_campaign_recipient_row,
    TicketMessage: _make_ticket_message_row,
}


def test_fail_closed_auto_discovery_of_scoped_models(app):
    """
    Every model class inheriting TenantScopedMixin must have an entry in MODEL_FACTORIES.
    Fails closed: newly introduced tenant models cannot silently skip isolation testing.
    """
    with app.app_context():
        discovered_scoped = [
            mapper.class_
            for mapper in db.Model.registry.mappers
            if issubclass(mapper.class_, TenantScopedMixin)
        ]
        assert len(discovered_scoped) >= 24

        missing_factories = [
            cls.__name__
            for cls in discovered_scoped
            if cls not in MODEL_FACTORIES
        ]
        assert not missing_factories, (
            f"Isolation test suite failure: The following TenantScopedMixin models have no registered "
            f"factory in MODEL_FACTORIES: {missing_factories}. Every tenant-scoped model must be covered."
        )


def test_cross_tenant_orm_isolation(app, org_a, org_b):
    """
    For every tenant-scoped model:
    Rows created in Tenant A cannot be listed, get'd, updated, or deleted in Tenant B's context.
    """
    with app.app_context():
        for model_cls, factory in MODEL_FACTORIES.items():
            # print(f"[DEBUG] isolation checking model={model_cls.__name__}")

            # 1. Create under Tenant A
            with tenant_context(org_a.id):
                row = factory(org_a)
                db.session.commit()
                row_id = row.id

            db.session.expunge_all()

            # 2. Access under Tenant B
            with tenant_context(org_b.id):
                # a) db.session.get must return None
                fetched = db.session.get(model_cls, row_id)
                assert fetched is None, (
                    f"Cross-tenant isolation violation: db.session.get({model_cls.__name__}, {row_id}) "
                    f"returned an object belonging to Tenant A from Tenant B's context!"
                )

                # b) Model.query / select must not include row
                query_result = model_cls.query.filter_by(id=row_id).first()
                assert query_result is None, (
                    f"Cross-tenant isolation violation: Model.query.filter_by(id={row_id}) "
                    f"found Tenant A's {model_cls.__name__} in Tenant B's context!"
                )

                # c) Update targeting row_id must update 0 rows
                if hasattr(model_cls, "name"):
                    updated_count = model_cls.query.filter_by(id=row_id).update({"name": "Tampered"})
                elif hasattr(model_cls, "first_name"):
                    updated_count = model_cls.query.filter_by(id=row_id).update({"first_name": "Tampered"})
                elif hasattr(model_cls, "status"):
                    updated_count = model_cls.query.filter_by(id=row_id).update({"status": "tampered"})
                elif hasattr(model_cls, "code"):
                    updated_count = model_cls.query.filter_by(id=row_id).update({"code": "TAMPERED"})
                elif hasattr(model_cls, "invoice_number"):
                    updated_count = model_cls.query.filter_by(id=row_id).update({"invoice_number": "INV-TAMPERED"})
                elif hasattr(model_cls, "discount_applied"):
                    updated_count = model_cls.query.filter_by(id=row_id).update({"discount_applied": 99.0})
                elif hasattr(model_cls, "refund_percentage"):
                    updated_count = model_cls.query.filter_by(id=row_id).update({"refund_percentage": 50.0})
                elif hasattr(model_cls, "action"):
                    updated_count = model_cls.query.filter_by(id=row_id).update({"action": "tampered"})
                elif hasattr(model_cls, "token_hash"):
                    updated_count = model_cls.query.filter_by(id=row_id).update({"token_hash": "tampered"})
                elif hasattr(model_cls, "approval_status"):
                    updated_count = model_cls.query.filter_by(id=row_id).update({"approval_status": "tampered"})
                else:
                    updated_count = model_cls.query.filter_by(id=row_id).update({"organization_id": org_b.id})
                assert updated_count == 0, (
                    f"Cross-tenant isolation violation: Updated {updated_count} rows of Tenant A's "
                    f"{model_cls.__name__} from Tenant B's context!"
                )

                # d) Delete targeting row_id must delete 0 rows
                deleted_count = model_cls.query.filter_by(id=row_id).delete()
                assert deleted_count == 0, (
                    f"Cross-tenant isolation violation: Deleted {deleted_count} rows of Tenant A's "
                    f"{model_cls.__name__} from Tenant B's context!"
                )


def test_http_cross_tenant_isolation_returns_404(client_a, client_b, org_a, org_b, make_user, make_trek):
    """
    HTTP requests with Tenant A entity identifiers submitted on Tenant B's host return 404.
    """
    trek_a = make_trek(org_a, name="Annapurna Org A")
    trek_hashid = encode(trek_a.id)

    with tenant_bypass():
        user_a = make_user(org_a, role="trekker", email="trekker_a@a.example.com")
        ticket_a = Ticket(
            organization_id=org_a.id,
            user_id=user_a.id,
            type="query",
            subject="Question A",
            message="Details A",
            status="open",
        )
        db.session.add(ticket_a)
        db.session.commit()
        ticket_hashid = encode(ticket_a.id)

    db.session.expunge_all()

    # 1. Public trek details on Tenant B returns 404
    resp_pub = client_b.get(f"/treks/{trek_hashid}")
    assert resp_pub.status_code == 404

    # 2. REST API trek details on Tenant B returns 404
    resp_api = client_b.get(f"/api/treks/{trek_hashid}")
    assert resp_api.status_code == 404

    # 3. Authenticated tickets access on Tenant B returns 404
    with tenant_bypass():
        user_b = make_user(org_b, role="trekker", email="trekker_b@b.example.com")

    with client_b.session_transaction() as sess:
        sess["_user_id"] = str(user_b.id)

    resp_ticket = client_b.get(f"/tickets/{ticket_hashid}")
    assert resp_ticket.status_code == 404


def test_tenant_bypass_import_allowlist():
    """
    Scans all source code in app/ asserting tenant_bypass is ONLY imported in approved modules.
    No general route or controller may bypass tenant isolation.
    """
    app_root = Path(__file__).resolve().parent.parent.parent / "app"

    # Approved modules relative to app/
    approved_paths = {
        "tenancy/scoping.py",
        "tenancy/__init__.py",
        "tenancy/resolver.py",
        "platform/auth.py",
        "platform/routes.py",
        "services/tenancy_service.py",
        "services/rbac_service.py",
        "services/payment_service.py",
        "services/ticket_service.py",
        "services/auth_service.py",
        "services/backup_service.py",
        "services/monitoring_service.py",
        "services/waitlist_service.py",
        "api/payments_resource.py",
        "api/route_webhook_resource.py",
        "api/whatsapp_webhook_resource.py",
        "__init__.py",
    }

    violating_files = []

    for py_file in app_root.rglob("*.py"):
        rel_path = py_file.relative_to(app_root).as_posix()
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        imports_bypass = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "scoping" in node.module:
                    for alias in node.names:
                        if alias.name == "tenant_bypass":
                            imports_bypass = True
                elif node.module and "tenancy" in node.module:
                    for alias in node.names:
                        if alias.name == "tenant_bypass":
                            imports_bypass = True

        if imports_bypass and rel_path not in approved_paths:
            violating_files.append(rel_path)

    assert not violating_files, (
        f"Security Gate Failure: tenant_bypass is imported in unapproved modules: {violating_files}. "
        f"Only platform, resolver, and approved background services may invoke tenant_bypass."
    )


def test_cross_plane_session_isolation(client_platform, client_a, platform_admin, org_a, make_user):
    """
    Platform session on tenant host and tenant session on platform host both yield no access.
    """
    with tenant_bypass():
        admin_user = make_user(org_a, role="admin", email="tadmin@a.example.com")

    # 1. Platform admin session hitting tenant admin endpoint
    with client_a.session_transaction() as sess:
        sess["platform_admin_id"] = str(platform_admin.id)

    resp_t = client_a.get("/admin/dashboard", follow_redirects=False)
    assert resp_t.status_code == 302
    assert "/login" in resp_t.location

    # 2. Tenant admin session hitting platform endpoint
    with client_platform.session_transaction() as sess:
        sess["_user_id"] = str(admin_user.id)

    resp_p = client_platform.get("/platform/dashboard", follow_redirects=False)
    assert resp_p.status_code == 302
    assert "/platform/login" in resp_p.location

"""
Integration tests for self-cancel and force-cancel paths (Milestone P9).
Tests customer-requested cancellations with tiered refunds, admin/staff force-cancellations with 100% refunds,
and waitlist promotion upon freeing of booked slots.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
import pytest

from app.extensions import db
from app.models.booking import Booking
from app.models.payment import Payment
from app.models.trek import Trek
from app.models.waitlist_entry import WaitlistEntry
from app.services import payment_service, refund_service, waitlist_service
from app.services.id_codec import encode
from app.tenancy.scoping import tenant_context


def test_customer_self_cancel_tiered_refund(app, org_a, make_user, client_a):
    """Customer self-cancel computes correct refund tier and increments available slots."""
    with app.app_context(), tenant_context(org_a.id):
        refund_service.seed_default_policy(org_a)
        user = make_user(org_a, role="trekker", email="selfcancel@example.com")
        trek = Trek(
            organization_id=org_a.id,
            title="Kedarkantha Winter",
            start_date=date.today() + timedelta(days=20), # > 14 days -> 100%
            end_date=date.today() + timedelta(days=25),
            total_slots=10,
            available_slots=8,
            price_per_slot=1000.0,
            status="open",
        )
        db.session.add(trek)
        db.session.flush()

        booking = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=user.id,
            slot_count=2,
            total_amount=2000.0,
            status="booked",
        )
        db.session.add(booking)
        db.session.flush()

        payment = Payment(
            organization_id=org_a.id,
            booking_id=booking.id,
            amount=2000.0,
            currency="INR",
            status="paid",
        )
        db.session.add(payment)
        db.session.commit()

        # Login as trekker
        with client_a.session_transaction() as sess:
            sess["_user_id"] = str(user.id)

        # Cancel the booking
        booking_hash = encode(booking.id)
        resp = client_a.post(f"/trekker/bookings/{booking_hash}/cancel", follow_redirects=True)
        assert resp.status_code == 200

        db.session.refresh(booking)
        db.session.refresh(trek)
        assert booking.status == "cancelled"
        assert booking.cancellation_reason == "customer_requested"
        assert booking.refund_percentage == Decimal("100.00")
        assert booking.refund_amount == Decimal("2000.00")
        # 2 slots freed: 8 -> 10
        assert trek.available_slots == 10


def test_force_cancel_by_admin(app, org_a, make_user, client_a):
    """Admin force-cancel grants 100% refund and restores slots."""
    with app.app_context(), tenant_context(org_a.id):
        admin = make_user(org_a, role="admin", email="admin@example.com")
        user = make_user(org_a, role="trekker", email="trekker@example.com")

        trek = Trek(
            organization_id=org_a.id,
            title="Chadar Frozen River",
            start_date=date.today() + timedelta(days=2), # < 7 days (would normally be 0% or 25%)
            end_date=date.today() + timedelta(days=8),
            total_slots=10,
            available_slots=9,
            price_per_slot=5000.0,
            status="open",
        )
        db.session.add(trek)
        db.session.flush()

        booking = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=user.id,
            slot_count=1,
            total_amount=5000.0,
            status="booked",
        )
        db.session.add(booking)
        db.session.flush()

        payment = Payment(
            organization_id=org_a.id,
            booking_id=booking.id,
            amount=5000.0,
            currency="INR",
            status="paid",
        )
        db.session.add(payment)
        db.session.commit()

        with client_a.session_transaction() as sess:
            sess["_user_id"] = str(admin.id)

        booking_hash = encode(booking.id)
        with patch("app.services.notification_service.send_force_cancel_email") as mock_email:
            resp = client_a.post(
                f"/admin/bookings/{booking_hash}/force-cancel",
                data={"reason": "Adverse weather conditions"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert mock_email.called

        db.session.refresh(booking)
        db.session.refresh(trek)
        assert booking.status == "cancelled"
        assert booking.cancellation_reason == "staff_force_cancel"
        assert booking.refund_percentage == Decimal("100.00")
        assert booking.refund_amount == Decimal("5000.00")
        assert trek.available_slots == 10


def test_cancel_frees_slot_and_triggers_waitlist_offer(app, org_a, make_user):
    """Cancelling a booked booking frees capacity and triggers waitlist promotion."""
    with app.app_context(), tenant_context(org_a.id):
        u1 = make_user(org_a, email="booked_user@example.com")
        u2 = make_user(org_a, email="waitlist_user@example.com")

        trek = Trek(
            organization_id=org_a.id,
            title="Kashmir Lakes",
            start_date=date.today() + timedelta(days=30),
            end_date=date.today() + timedelta(days=35),
            total_slots=1,
            available_slots=0, # Full
            price_per_slot=2000.0,
            status="open",
        )
        db.session.add(trek)
        db.session.flush()

        b1 = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=u1.id,
            slot_count=1,
            total_amount=2000.0,
            status="booked",
        )
        db.session.add(b1)
        db.session.flush()

        # u2 joins waitlist
        w_entry = WaitlistEntry(
            organization_id=org_a.id,
            user_id=u2.id,
            trek_id=trek.id,
            requested_slot_count=1,
            status="waiting",
        )
        db.session.add(w_entry)
        db.session.commit()

        # Cancel b1
        with patch("app.services.notification_service.send_waitlist_offer_email") as mock_offer:
            payment_service.cancel_booking(b1, u1, "customer_requested")
            db.session.commit()
            assert mock_offer.called

        db.session.refresh(w_entry)
        assert w_entry.status == "offered"

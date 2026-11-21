"""
Integration tests for manual/offline bookings end-to-end (Milestone P9).
Tests toggle enforcement, queue review, sign-off, slot deduction, and rejection.
"""

from datetime import date, timedelta
import pytest

from app.extensions import db
from app.models.booking import Booking
from app.models.trek import Trek
from app.models.user import User
from app.services import payment_service
from app.services.id_codec import encode
from app.tenancy.scoping import tenant_context


def test_manual_booking_end_to_end(app, org_a, make_user, client_a):
    """Staff creates manual booking, admin signs off, slots decrement, booking confirms."""
    with app.app_context(), tenant_context(org_a.id):
        # Setup tenant owner admin and staff
        owner = make_user(org_a, role="admin", email="owner@example.com")
        org_a.tenant_admin_user_id = owner.id
        org_a.manual_booking_enabled = True

        staff = make_user(org_a, role="staff", email="staff@example.com")
        staff.set_capabilities(["bookings.manual_create"])
        db.session.commit()

        trek = Trek(
            organization_id=org_a.id,
            title="Hampta Pass Trek",
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=15),
            total_slots=5,
            available_slots=5,
            price_per_slot=1000.0,
            status="open",
        )
        db.session.add(trek)
        db.session.commit()

        # 1. Staff creates manual booking via POST /staff/bookings/manual
        with client_a.session_transaction() as sess:
            sess["_user_id"] = str(staff.id)

        trek_hash = encode(trek.id)
        resp = client_a.post(
            "/staff/bookings/manual",
            data={
                "trek_id": trek_hash,
                "customer_name": "Walk-in Customer",
                "customer_email": "walkin@example.com",
                "mobile_country_code": "+91",
                "mobile_number": "9876543210",
                "slot_count": 2,
                "payment_mode": "cash",
                "payment_reference": "CASH-REC-001",
                "notes": "Paid in cash at base camp",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        # Booking should exist in status 'pending', booking_channel 'manual'
        manual_b = Booking.query.filter_by(
            organization_id=org_a.id,
            booking_channel="manual",
            status="pending",
        ).first()
        assert manual_b is not None
        assert manual_b.slot_count == 2
        # Slots not decremented yet
        db.session.refresh(trek)
        assert trek.available_slots == 5

        # 2. Staff cannot access sign-off queue
        resp_staff_queue = client_a.get("/admin/manual-bookings")
        assert resp_staff_queue.status_code == 403

        # 3. Owner accesses queue and signs off
        with client_a.session_transaction() as sess:
            sess["_user_id"] = str(owner.id)

        resp_queue = client_a.get("/admin/manual-bookings")
        assert resp_queue.status_code == 200
        assert b"Walk-in Customer" in resp_queue.data

        b_hash = encode(manual_b.id)
        resp_signoff = client_a.post(
            f"/admin/bookings/{b_hash}/manual-sign-off",
            follow_redirects=True,
        )
        assert resp_signoff.status_code == 200

        db.session.refresh(manual_b)
        db.session.refresh(trek)
        assert manual_b.status == "booked"
        assert manual_b.manual_sign_off_by == owner.id
        assert trek.available_slots == 3  # 5 - 2 = 3


def test_manual_booking_rejected(app, org_a, make_user, client_a):
    """Admin can reject a pending manual booking."""
    with app.app_context(), tenant_context(org_a.id):
        owner = make_user(org_a, role="admin", email="owner2@example.com")
        org_a.tenant_admin_user_id = owner.id
        org_a.manual_booking_enabled = True

        u = make_user(org_a, email="cust@example.com")
        trek = Trek(
            organization_id=org_a.id,
            title="Trek 2",
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=15),
            total_slots=5,
            available_slots=5,
            price_per_slot=1000.0,
            status="open",
        )
        db.session.add(trek)
        db.session.flush()

        b = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=u.id,
            slot_count=1,
            total_amount=1000.0,
            status="pending",
            booking_channel="manual",
        )
        db.session.add(b)
        db.session.commit()

        with client_a.session_transaction() as sess:
            sess["_user_id"] = str(owner.id)

        b_hash = encode(b.id)
        resp = client_a.post(
            f"/admin/bookings/{b_hash}/manual-reject",
            data={"reason": "Customer cancelled before payment arrived"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        db.session.refresh(b)
        assert b.status == "cancelled"

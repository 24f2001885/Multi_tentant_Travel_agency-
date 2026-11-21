"""
Unit tests for app/services/refund_service.py (Milestone P9).
Tests tiered refund calculations, default policy seeding, and policy validation.
"""

from datetime import date, timedelta
from decimal import Decimal
import pytest

from app.extensions import db
from app.models.booking import Booking
from app.models.payment import Payment
from app.models.refund_policy import RefundPolicy
from app.models.trek import Trek
from app.services import refund_service
from app.services.refund_service import PolicyValidationError
from app.tenancy.scoping import tenant_context


def test_seed_default_policy(app, org_a):
    """Verifies idempotent seeding of default refund policy tiers."""
    with app.app_context(), tenant_context(org_a.id):
        refund_service.seed_default_policy(org_a)
        db.session.commit()

        policies = RefundPolicy.query.filter_by(organization_id=org_a.id).order_by(
            RefundPolicy.min_days_before_trip.desc()
        ).all()
        assert len(policies) == 4

        # Idempotent: second call creates no duplicates
        refund_service.seed_default_policy(org_a)
        db.session.commit()
        assert RefundPolicy.query.filter_by(organization_id=org_a.id).count() == 4


def test_calculate_refund_staff_force_cancel(app, org_a, make_user):
    """Force cancel and identity verification failure return 100% unconditionally."""
    with app.app_context(), tenant_context(org_a.id):
        u = make_user(org_a)
        trek = Trek(
            organization_id=org_a.id,
            title="Himachal Trek",
            start_date=date.today() + timedelta(days=2),
            end_date=date.today() + timedelta(days=5),
            total_slots=10,
            available_slots=10,
            price_per_slot=1000.0,
            status="open",
        )
        db.session.add(trek)
        db.session.flush()

        booking = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=u.id,
            slot_count=1,
            total_amount=1000.0,
            status="booked",
        )
        db.session.add(booking)
        db.session.flush()

        pct = refund_service.calculate_refund(booking, "staff_force_cancel")
        assert pct == Decimal("100.00")

        pct_id = refund_service.calculate_refund(booking, "identity_verification_failed")
        assert pct_id == Decimal("100.00")


def test_tiered_refund_customer_requested(app, org_a, make_user):
    """Customer-requested cancel uses tiered policy lookup by days_until_trip."""
    with app.app_context(), tenant_context(org_a.id):
        refund_service.seed_default_policy(org_a)
        u = make_user(org_a)

        # 20 days out -> 100%
        trek_far = Trek(
            organization_id=org_a.id,
            title="Far Trek",
            start_date=date(2026, 11, 20),
            end_date=date(2026, 11, 25),
            total_slots=10,
            available_slots=10,
            price_per_slot=1000.0,
            status="open",
        )
        db.session.add(trek_far)
        db.session.flush()
        b_far = Booking(
            organization_id=org_a.id,
            trek_id=trek_far.id,
            user_id=u.id,
            slot_count=1,
            total_amount=1000.0,
            status="booked",
        )
        db.session.add(b_far)
        db.session.flush()

        today = date(2026, 11, 1) # 19 days before start
        pct = refund_service.calculate_refund(b_far, "customer_requested", today=today)
        assert pct == Decimal("100.00")

        # 10 days out -> 50%
        today_10 = date(2026, 11, 10)
        pct_10 = refund_service.calculate_refund(b_far, "customer_requested", today=today_10)
        assert pct_10 == Decimal("50.00")

        # 3 days out -> 25%
        today_3 = date(2026, 11, 17)
        pct_3 = refund_service.calculate_refund(b_far, "customer_requested", today=today_3)
        assert pct_3 == Decimal("25.00")

        # Trip start day -> 0%
        today_0 = date(2026, 11, 20)
        pct_0 = refund_service.calculate_refund(b_far, "customer_requested", today=today_0)
        assert pct_0 == Decimal("0.00")


def test_preview_refund(app, org_a, make_user):
    """preview_refund computes percentage and monetary amount accurately."""
    with app.app_context(), tenant_context(org_a.id):
        refund_service.seed_default_policy(org_a)
        u = make_user(org_a)
        trek = Trek(
            organization_id=org_a.id,
            title="Preview Trek",
            start_date=date(2026, 11, 15),
            end_date=date(2026, 11, 20),
            total_slots=10,
            available_slots=10,
            price_per_slot=2000.0,
            status="open",
        )
        db.session.add(trek)
        db.session.flush()

        b = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=u.id,
            slot_count=2,
            total_amount=4000.0,
            status="booked",
        )
        db.session.add(b)
        db.session.flush()

        pmt = Payment(
            organization_id=org_a.id,
            booking_id=b.id,
            amount=4000.0,
            currency="INR",
            status="paid",
        )
        db.session.add(pmt)
        db.session.flush()

        # 10 days before -> 50% of 4000 = 2000
        pct, amt = refund_service.preview_refund(b, today=date(2026, 11, 5))
        assert pct == Decimal("50.00")
        assert amt == Decimal("2000.00")


def test_validate_and_replace_policy(app, org_a, make_user):
    """Validates rows and ensures overlapping tiers or invalid percentages raise PolicyValidationError."""
    with app.app_context(), tenant_context(org_a.id):
        admin = make_user(org_a, role="admin")

        # Overlapping ranges
        bad_rows = [
            {"min_days": 10, "max_days": 20, "pct": 80},
            {"min_days": 15, "max_days": 30, "pct": 50},
        ]
        with pytest.raises(PolicyValidationError):
            refund_service.replace_policy(org_a, bad_rows, admin)

        # Invalid percentage > 100
        bad_pct = [
            {"min_days": 10, "max_days": 20, "pct": 120},
        ]
        with pytest.raises(PolicyValidationError):
            refund_service.replace_policy(org_a, bad_pct, admin)

        # Valid replacement
        valid_rows = [
            {"min_days": 30, "max_days": None, "pct": 90},
            {"min_days": 15, "max_days": 29, "pct": 50},
            {"min_days": 0, "max_days": 14, "pct": 0},
        ]
        refund_service.replace_policy(org_a, valid_rows, admin)
        db.session.commit()

        policies = RefundPolicy.query.filter_by(organization_id=org_a.id).all()
        assert len(policies) == 3

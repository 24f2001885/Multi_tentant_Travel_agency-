"""
Unit tests for app/services/coupon_service.py (Milestone P9).
Tests coupon validation, discount computation with minimum floor, redemption, and admin CRUD.
"""

from datetime import date, timedelta
from decimal import Decimal
import pytest

from app.extensions import db
from app.models.booking import Booking
from app.models.coupon import Coupon
from app.models.coupon_redemption import CouponRedemption
from app.models.trek import Trek
from app.services import coupon_service
from app.services.coupon_service import CouponInvalidError, CouponValidationError
from app.tenancy.scoping import tenant_context


def test_validate_coupon_success_and_errors(app, org_a, make_user):
    """Verifies all coupon validation failure states and success."""
    with app.app_context(), tenant_context(org_a.id):
        u = make_user(org_a)
        trek = Trek(
            organization_id=org_a.id,
            title="Trek with Coupon",
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=15),
            total_slots=10,
            available_slots=10,
            price_per_slot=1000.0,
            status="open",
        )
        db.session.add(trek)

        # 1. Valid coupon
        c_valid = Coupon(
            organization_id=org_a.id,
            code="SAVE10",
            discount_type="percentage",
            discount_value=Decimal("10.00"),
            is_active=True,
        )
        # 2. Inactive coupon
        c_inactive = Coupon(
            organization_id=org_a.id,
            code="INACTIVE",
            discount_type="percentage",
            discount_value=Decimal("10.00"),
            is_active=False,
        )
        # 3. Expired coupon
        c_expired = Coupon(
            organization_id=org_a.id,
            code="EXPIRED",
            discount_type="flat",
            discount_value=Decimal("100.00"),
            valid_until=date.today() - timedelta(days=1),
            is_active=True,
        )
        # 4. Exhausted coupon
        c_exhausted = Coupon(
            organization_id=org_a.id,
            code="EXHAUSTED",
            discount_type="flat",
            discount_value=Decimal("50.00"),
            max_uses=2,
            used_count=2,
            is_active=True,
        )
        # 5. Min amount coupon
        c_min = Coupon(
            organization_id=org_a.id,
            code="MIN500",
            discount_type="flat",
            discount_value=Decimal("50.00"),
            min_booking_amount=Decimal("500.00"),
            is_active=True,
        )
        db.session.add_all([c_valid, c_inactive, c_expired, c_exhausted, c_min])
        db.session.commit()

        # Success
        assert coupon_service.validate_coupon("save10", u, trek, Decimal("1000.00")).id == c_valid.id

        # Unknown
        with pytest.raises(CouponInvalidError) as exc_unknown:
            coupon_service.validate_coupon("NOEXIST", u, trek, Decimal("1000.00"))
        assert exc_unknown.value.reason == "unknown"

        # Inactive
        with pytest.raises(CouponInvalidError) as exc_inactive:
            coupon_service.validate_coupon("INACTIVE", u, trek, Decimal("1000.00"))
        assert exc_inactive.value.reason == "inactive"

        # Expired
        with pytest.raises(CouponInvalidError) as exc_expired:
            coupon_service.validate_coupon("EXPIRED", u, trek, Decimal("1000.00"))
        assert exc_expired.value.reason == "expired"

        # Exhausted
        with pytest.raises(CouponInvalidError) as exc_exhausted:
            coupon_service.validate_coupon("EXHAUSTED", u, trek, Decimal("1000.00"))
        assert exc_exhausted.value.reason == "exhausted"

        # Below minimum
        with pytest.raises(CouponInvalidError) as exc_min:
            coupon_service.validate_coupon("MIN500", u, trek, Decimal("300.00"))
        assert exc_min.value.reason == "below_minimum"


def test_compute_discount_minimum_floor(app, org_a):
    """Discount calculation must cap so payable is never below ₹1.00."""
    with app.app_context(), tenant_context(org_a.id):
        # 100% off coupon
        c_100 = Coupon(
            organization_id=org_a.id,
            code="FREE100",
            discount_type="percentage",
            discount_value=Decimal("100.00"),
            is_active=True,
        )
        # ₹500 flat off coupon
        c_flat500 = Coupon(
            organization_id=org_a.id,
            code="FLAT500",
            discount_type="flat",
            discount_value=Decimal("500.00"),
            is_active=True,
        )

        # Base amount ₹100: 100% discount is capped at ₹99.00 so payable is ₹1.00
        disc = coupon_service.compute_discount(c_100, Decimal("100.00"))
        assert disc == Decimal("99.00")

        # Base amount ₹200 with ₹500 coupon: capped at ₹199.00 so payable is ₹1.00
        disc_flat = coupon_service.compute_discount(c_flat500, Decimal("200.00"))
        assert disc_flat == Decimal("199.00")

        # Base amount ₹1000 with ₹500 coupon: ₹500.00
        disc_normal = coupon_service.compute_discount(c_flat500, Decimal("1000.00"))
        assert disc_normal == Decimal("500.00")


def test_redeem_coupon(app, org_a, make_user):
    """Redeeming a coupon increments used_count and creates CouponRedemption."""
    with app.app_context(), tenant_context(org_a.id):
        u = make_user(org_a)
        trek = Trek(
            organization_id=org_a.id,
            title="Redemption Trek",
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=15),
            total_slots=10,
            available_slots=10,
            price_per_slot=1000.0,
            status="open",
        )
        db.session.add(trek)

        c = Coupon(
            organization_id=org_a.id,
            code="REDEEM20",
            discount_type="percentage",
            discount_value=Decimal("20.00"),
            max_uses=5,
            used_count=0,
            is_active=True,
        )
        db.session.add(c)
        db.session.flush()

        booking = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=u.id,
            slot_count=1,
            total_amount=800.0,
            status="booked",
            coupon_id=c.id,
        )
        db.session.add(booking)
        db.session.flush()

        redemption = coupon_service.redeem_coupon(c, booking, u, Decimal("200.00"))
        db.session.commit()

        assert redemption.id is not None
        assert redemption.discount_applied == Decimal("200.00")
        assert c.used_count == 1


def test_create_coupon_validation(app, org_a, make_user):
    """Validation rejects invalid discount values or inverted date ranges."""
    with app.app_context(), tenant_context(org_a.id):
        admin = make_user(org_a, role="admin")

        # Percentage > 100
        with pytest.raises(CouponValidationError):
            coupon_service.create_coupon(
                org_a,
                {"code": "OVER100", "discount_type": "percentage", "discount_value": 150},
                actor=admin,
            )

        # Inverted dates
        with pytest.raises(CouponValidationError):
            coupon_service.create_coupon(
                org_a,
                {
                    "code": "BAD_DATES",
                    "discount_type": "flat",
                    "discount_value": 50,
                    "valid_from": date(2026, 12, 1),
                    "valid_until": date(2026, 11, 1),
                },
                actor=admin,
            )

        # Valid creation
        c = coupon_service.create_coupon(
            org_a,
            {"code": "WELCOME", "discount_type": "flat", "discount_value": 100},
            actor=admin,
        )
        db.session.commit()
        assert c.id is not None
        assert c.code == "WELCOME"

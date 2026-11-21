"""
Coupon validation, discount computation, redemption, and CRUD (TDR v2 §4.4; PDR v2 §5.9).
Framework-agnostic.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import json
from typing import Any, Dict, Optional
import uuid

from app.extensions import db
from app.models.coupon import Coupon
from app.models.coupon_redemption import CouponRedemption
from app.models.audit_log import AuditLog


class CouponInvalidError(Exception):
    """
    Raised when a coupon cannot be redeemed.
    Carries specific reason code: 'unknown', 'inactive', 'expired', 'exhausted', 'below_minimum'.
    """
    def __init__(self, reason: str, message: Optional[str] = None):
        self.reason = reason
        super().__init__(message or f"Coupon invalid: {reason}")


class CouponValidationError(Exception):
    """Raised when coupon configuration/creation input is invalid."""
    pass


def validate_coupon(code: str, user, trek, base_amount: Decimal) -> Coupon:
    """
    Validates a coupon code within the organization context.
    Raises CouponInvalidError with reason code on failure.
    """
    if not code:
        raise CouponInvalidError("unknown", "No coupon code provided.")

    clean_code = code.strip().upper()
    org_id = getattr(trek, "organization_id", None)
    if not org_id and hasattr(user, "organization_id"):
        org_id = user.organization_id

    coupon = Coupon.query.filter_by(organization_id=org_id, code=clean_code).first()
    if not coupon:
        raise CouponInvalidError("unknown", f"Coupon '{clean_code}' not found.")

    if not coupon.is_active:
        raise CouponInvalidError("inactive", f"Coupon '{clean_code}' is inactive.")

    today = date.today()
    if coupon.valid_from and today < coupon.valid_from:
        raise CouponInvalidError("expired", f"Coupon '{clean_code}' is not yet valid (starts on {coupon.valid_from}).")
    if coupon.valid_until and today > coupon.valid_until:
        raise CouponInvalidError("expired", f"Coupon '{clean_code}' expired on {coupon.valid_until}.")

    if coupon.max_uses is not None and coupon.used_count >= coupon.max_uses:
        raise CouponInvalidError("exhausted", f"Coupon '{clean_code}' has reached its usage limit.")

    if coupon.min_booking_amount is not None and base_amount < coupon.min_booking_amount:
        raise CouponInvalidError(
            "below_minimum",
            f"Booking amount ₹{base_amount} is below coupon minimum of ₹{coupon.min_booking_amount}.",
        )

    return coupon


def compute_discount(coupon: Coupon, base_amount: Decimal) -> Decimal:
    """
    Calculates applicable discount in INR.
    Caps discount so payable amount never drops below ₹1.00 (payment gateway minimum).
    """
    if coupon.discount_type == "percentage":
        raw = base_amount * (Decimal(str(coupon.discount_value)) / Decimal("100.00"))
    else:
        raw = Decimal(str(coupon.discount_value))

    # Payable must be at least ₹1.00
    max_discount = max(Decimal("0.00"), base_amount - Decimal("1.00"))
    final_discount = min(raw, max_discount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return final_discount


def redeem_coupon(coupon: Coupon, booking, user, discount: Decimal) -> CouponRedemption:
    """
    Atomically increments usage and creates redemption record.
    Called inside confirm_payment winner path under lock.
    """
    # Row-lock coupon record
    locked_coupon = Coupon.query.with_for_update().filter_by(id=coupon.id).first()
    if not locked_coupon:
        locked_coupon = coupon

    locked_coupon.used_count += 1

    redemption = CouponRedemption(
        organization_id=booking.organization_id,
        coupon_id=locked_coupon.id,
        booking_id=booking.id,
        user_id=user.id,
        discount_applied=discount,
    )
    db.session.add(redemption)
    db.session.flush()
    return redemption


def create_coupon(org, data: Dict[str, Any], actor=None) -> Coupon:
    """
    Admin helper to create a discount coupon.
    """
    code = (data.get("code") or "").strip().upper()
    if not code:
        raise CouponValidationError("Coupon code cannot be empty.")

    discount_type = data.get("discount_type")
    if discount_type not in ("percentage", "flat"):
        raise CouponValidationError("Discount type must be 'percentage' or 'flat'.")

    try:
        val = Decimal(str(data.get("discount_value", 0)))
    except Exception:
        raise CouponValidationError("Invalid discount value.")

    if val <= 0:
        raise CouponValidationError("Discount value must be positive.")
    if discount_type == "percentage" and val > 100:
        raise CouponValidationError("Percentage discount cannot exceed 100%.")

    valid_from = data.get("valid_from")
    valid_until = data.get("valid_until")
    if valid_from and valid_until and valid_until < valid_from:
        raise CouponValidationError("valid_until cannot be earlier than valid_from.")

    max_uses = data.get("max_uses")
    max_uses = int(max_uses) if max_uses not in (None, "", "null") else None
    min_amount = data.get("min_booking_amount")
    min_amount = Decimal(str(min_amount)) if min_amount not in (None, "", "null") else None

    # Check for existing code in organization
    existing = Coupon.query.filter_by(organization_id=org.id, code=code).first()
    if existing:
        raise CouponValidationError(f"Coupon code '{code}' already exists in this organization.")

    coupon = Coupon(
        organization_id=org.id,
        code=code,
        discount_type=discount_type,
        discount_value=val,
        valid_from=valid_from,
        valid_until=valid_until,
        max_uses=max_uses,
        min_booking_amount=min_amount,
        is_active=bool(data.get("is_active", True)),
    )
    db.session.add(coupon)
    db.session.flush()

    if actor:
        log = AuditLog(
            organization_id=org.id,
            user_id=getattr(actor, "id", None),
            action="coupon.create",
            target_type="coupon",
            target_id=0,
            details=json.dumps({"code": code, "type": discount_type, "value": str(val)}),
        )
        db.session.add(log)

    return coupon


def update_coupon(coupon: Coupon, data: Dict[str, Any], actor=None) -> Coupon:
    """
    Admin helper to edit an existing coupon.
    Code cannot be edited if the coupon has already been redeemed.
    """
    new_code = (data.get("code") or "").strip().upper()
    if new_code and new_code != coupon.code:
        if coupon.used_count > 0:
            raise CouponValidationError("Cannot modify the coupon code after it has been redeemed.")
        coupon.code = new_code

    if "discount_type" in data:
        dt = data["discount_type"]
        if dt not in ("percentage", "flat"):
            raise CouponValidationError("Invalid discount type.")
        coupon.discount_type = dt

    if "discount_value" in data:
        val = Decimal(str(data["discount_value"]))
        if val <= 0 or (coupon.discount_type == "percentage" and val > 100):
            raise CouponValidationError("Invalid discount value.")
        coupon.discount_value = val

    if "valid_from" in data:
        coupon.valid_from = data["valid_from"]
    if "valid_until" in data:
        coupon.valid_until = data["valid_until"]

    if coupon.valid_from and coupon.valid_until and coupon.valid_until < coupon.valid_from:
        raise CouponValidationError("valid_until cannot be earlier than valid_from.")

    if "max_uses" in data:
        mu = data["max_uses"]
        coupon.max_uses = int(mu) if mu not in (None, "", "null") else None

    if "min_booking_amount" in data:
        ma = data["min_booking_amount"]
        coupon.min_booking_amount = Decimal(str(ma)) if ma not in (None, "", "null") else None

    if "is_active" in data:
        coupon.is_active = bool(data["is_active"])

    db.session.flush()

    if actor:
        log = AuditLog(
            organization_id=coupon.organization_id,
            user_id=getattr(actor, "id", None),
            action="coupon.update",
            target_type="coupon",
            target_id=0,
            details=json.dumps({"coupon_id": str(coupon.id), "code": coupon.code}),
        )
        db.session.add(log)

    return coupon


def toggle_coupon_active(coupon: Coupon, actor=None) -> Coupon:
    """
    Toggles is_active on a coupon.
    """
    coupon.is_active = not coupon.is_active
    db.session.flush()

    if actor:
        log = AuditLog(
            organization_id=coupon.organization_id,
            user_id=getattr(actor, "id", None),
            action="coupon.toggle_active",
            target_type="coupon",
            target_id=0,
            details=json.dumps({"coupon_id": str(coupon.id), "is_active": coupon.is_active}),
        )
        db.session.add(log)

    return coupon

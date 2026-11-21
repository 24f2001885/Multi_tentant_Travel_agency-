"""
Tiered refund calculation + policy management (TDR v2 §11; PDR v2 §5.7, §5.9).
Framework-agnostic.
"""

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import json
from typing import Any, Dict, List, Optional, Tuple
import zoneinfo

from app.extensions import db
from app.models.refund_policy import RefundPolicy
from app.models.audit_log import AuditLog


class PolicyValidationError(Exception):
    """Raised when refund policy tier definitions overlap or violate constraints."""
    pass


class RefundNotAllowedError(Exception):
    """Raised when a booking is in a state where refunds are strictly forbidden."""
    pass


def _get_kolkata_today() -> date:
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")
    return datetime.now(tz).date()


def calculate_refund(booking, reason: str, today: Optional[date] = None) -> Decimal:
    """
    Calculates refund percentage (Decimal 0.00 to 100.00).
    - staff_force_cancel / identity_verification_failed -> 100% unconditionally.
    - customer_requested -> tiered lookup by days_until_trip. Gaps return 0%.
    """
    if reason in ("staff_force_cancel", "identity_verification_failed"):
        return Decimal("100.00")

    if today is None:
        today = _get_kolkata_today()

    trek = booking.trek
    if not trek or not trek.start_date:
        return Decimal("0.00")

    days_until_trip = (trek.start_date - today).days
    if days_until_trip < 0:
        return Decimal("0.00")

    # Match policy tier for the booking's organization
    policies = RefundPolicy.query.filter_by(organization_id=booking.organization_id).all()
    for pol in policies:
        min_d = pol.min_days_before_trip
        max_d = pol.max_days_before_trip
        if days_until_trip >= min_d:
            if max_d is None or days_until_trip <= max_d:
                return Decimal(str(pol.refund_percentage))

    return Decimal("0.00")


def _normalize_policy_row(r: Dict[str, Any]) -> Dict[str, Any]:
    min_days = r.get("min_days_before_trip")
    if min_days is None:
        min_days = r.get("min_days", 0)
    max_days = r.get("max_days_before_trip")
    if max_days is None:
        max_days = r.get("max_days")
    max_days = int(max_days) if max_days not in (None, "", "null") else None
    pct = r.get("refund_percentage")
    if pct is None:
        pct = r.get("pct", 0)
    return {
        "min_days_before_trip": int(min_days),
        "max_days_before_trip": max_days,
        "refund_percentage": Decimal(str(pct)),
    }


def preview_refund(booking, today: Optional[date] = None) -> Tuple[Decimal, Decimal]:
    """
    Returns (percentage, amount) for customer cancel confirmation UI.
    """
    pct = calculate_refund(booking, "customer_requested", today=today)
    total_paid = Decimal("0.00")

    # 1. Check booking payments (handles both 'paid' and 'success' transaction statuses)
    payments = getattr(booking, "payments", None)
    if not payments and getattr(booking, "id", None):
        from app.models.payment import Payment
        payments = Payment.query.filter_by(booking_id=booking.id).all()

    if payments:
        paid_pmts = [p for p in payments if getattr(p, "status", None) in ("paid", "success")]
        if paid_pmts:
            total_paid = sum((Decimal(str(p.amount)) for p in paid_pmts), Decimal("0.00"))

    # 2. Fallback to booking total_price or total_amount
    if total_paid == Decimal("0.00"):
        amount = getattr(booking, "total_price", None) or getattr(booking, "total_amount", None)
        if amount is not None:
            total_paid = Decimal(str(amount))

    # 3. Fallback to trek pricing * slot_count
    if total_paid == Decimal("0.00") and getattr(booking, "trek", None):
        trek_price = getattr(booking.trek, "price", None) or getattr(booking.trek, "price_per_slot", None)
        if trek_price is not None:
            slots = getattr(booking, "slot_count", 1) or 1
            total_paid = Decimal(str(trek_price)) * Decimal(str(slots))

    refund_amount = (total_paid * (pct / Decimal("100.00"))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return pct, refund_amount


def seed_default_policy(org) -> None:
    """
    Idempotently seeds standard refund tiers for an organization (PDR v2 §5.9):
    14+ days: 100%, 7-13 days: 50%, 1-6 days: 25%, 0 days: 0%.
    """
    existing = RefundPolicy.query.filter_by(organization_id=org.id).first()
    if existing is not None:
        return

    defaults = [
        {"min": 14, "max": None, "pct": Decimal("100.00")},
        {"min": 7, "max": 13, "pct": Decimal("50.00")},
        {"min": 1, "max": 6, "pct": Decimal("25.00")},
        {"min": 0, "max": 0, "pct": Decimal("0.00")},
    ]

    for d in defaults:
        pol = RefundPolicy(
            organization_id=org.id,
            min_days_before_trip=d["min"],
            max_days_before_trip=d["max"],
            refund_percentage=d["pct"],
        )
        db.session.add(pol)


def validate_policy_rows(rows: List[Dict[str, Any]]) -> None:
    """
    Validates that rows are internally consistent and non-overlapping.
    Raises PolicyValidationError with row index if invalid.
    """
    parsed_ranges = []
    for idx, raw_r in enumerate(rows, start=1):
        try:
            norm = _normalize_policy_row(raw_r)
            min_days = norm["min_days_before_trip"]
            max_days = norm["max_days_before_trip"]
            pct = norm["refund_percentage"]
        except (ValueError, TypeError) as e:
            raise PolicyValidationError(f"Row {idx} contains invalid numeric data: {e}")

        if min_days < 0:
            raise PolicyValidationError(f"Row {idx}: min_days_before_trip must be >= 0")
        if max_days is not None and max_days < min_days:
            raise PolicyValidationError(f"Row {idx}: max_days_before_trip cannot be less than min_days_before_trip")
        if pct < 0 or pct > 100:
            raise PolicyValidationError(f"Row {idx}: refund_percentage must be between 0 and 100")

        high = max_days if max_days is not None else float("inf")
        # Check overlaps against already parsed ranges
        for p_idx, p_min, p_high in parsed_ranges:
            if max(min_days, p_min) <= min(high, p_high):
                raise PolicyValidationError(f"Row {idx} [{min_days}-{max_days}] overlaps with Row {p_idx} [{p_min}-{p_high if p_high != float('inf') else 'inf'}]")

        parsed_ranges.append((idx, min_days, high))


def replace_policy(org, rows: List[Dict[str, Any]], actor=None) -> None:
    """
    Atomically validates and replaces an organization's refund policy tiers.
    Logs to AuditLog. Does not commit.
    """
    validate_policy_rows(rows)

    old_policies = RefundPolicy.query.filter_by(organization_id=org.id).all()
    old_repr = [
        {"min": p.min_days_before_trip, "max": p.max_days_before_trip, "pct": float(p.refund_percentage)}
        for p in old_policies
    ]

    for p in old_policies:
        db.session.delete(p)
    db.session.flush()

    new_repr = []
    for raw_r in rows:
        norm = _normalize_policy_row(raw_r)
        pol = RefundPolicy(
            organization_id=org.id,
            min_days_before_trip=norm["min_days_before_trip"],
            max_days_before_trip=norm["max_days_before_trip"],
            refund_percentage=norm["refund_percentage"],
        )
        db.session.add(pol)
        new_repr.append({
            "min": norm["min_days_before_trip"],
            "max": norm["max_days_before_trip"],
            "pct": float(norm["refund_percentage"]),
        })

    if actor is not None:
        log = AuditLog(
            organization_id=org.id,
            user_id=getattr(actor, "id", None),
            action="refund_policy.update",
            target_type="organization",
            target_id=0,
            details=json.dumps({"before": old_repr, "after": new_repr}),
        )
        db.session.add(log)

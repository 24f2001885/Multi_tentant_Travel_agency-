"""
Booking lifecycle service (TDR v2 §3.1, §3.2; Milestone P9).
Uses PostgreSQL row-level locking (SELECT ... FOR UPDATE) for concurrency safety.
Supports group bookings, coupons, and manual booking creation.
"""

from decimal import Decimal
import json
import os
from typing import Any, Dict, List, Optional

from flask import current_app

from app.extensions import db
from app.models.booking import Booking
from app.models.booking_participant import BookingParticipant
from app.models.trek import Trek
from app.models.user import User
from app.models.audit_log import AuditLog
from app.services import coupon_service, waitlist_service


class TrekUnavailableError(Exception):
    """Trek is not `open` or has insufficient available slots."""
    pass


class DuplicateActiveBookingError(Exception):
    """User already has a pending/booked row for this trek."""
    pass


class InvalidParticipantsError(Exception):
    """Raised when group booking participant details are invalid."""
    pass


class RouteNotLinkedError(Exception):
    """Raised when REQUIRE_ROUTE_LINKAGE is enabled but the organization has no verified Route ID."""
    pass


class ManualBookingDisabledError(Exception):
    """Raised when manual booking intake is attempted while disabled for the tenant."""
    pass


def initiate_booking(
    user_id: int,
    trek_id: int,
    participants: Optional[List[Dict[str, Any]]] = None,
    coupon_code: Optional[str] = None,
) -> Booking:
    """
    Initiates a booking under PostgreSQL row-level locking on the trek row.
    Supports group bookings (slot_count = len(participants)) and coupons.
    Slots are NOT decremented at initiation (TDR §3.4) — only confirmed at payment.
    """
    user = User.query.get(user_id)
    if not user:
        raise ValueError(f"User {user_id} not found.")

    # 1. Parse and validate participants
    if not participants:
        participants = [{"name": user.name, "age": None}]

    cleaned_participants = []
    for idx, p in enumerate(participants):
        name = (p.get("name") or "").strip()
        if not name:
            raise InvalidParticipantsError(f"Participant #{idx + 1} must have a valid name.")
        if len(name) > 150:
            raise InvalidParticipantsError(f"Participant name #{idx + 1} exceeds 150 characters.")
        age = p.get("age")
        if age not in (None, "", "null"):
            try:
                age = int(age)
                if age < 0 or age > 120:
                    raise InvalidParticipantsError(f"Participant #{idx + 1} age must be between 0 and 120.")
            except (ValueError, TypeError):
                raise InvalidParticipantsError(f"Participant #{idx + 1} age must be an integer.")
        else:
            age = None
        cleaned_participants.append({"name": name, "age": age})

    slot_count = len(cleaned_participants)
    if slot_count < 1:
        raise InvalidParticipantsError("A booking must contain at least one participant.")

    # 2. Row-level lock on the specific trek
    trek = Trek.query.with_for_update().filter_by(id=trek_id).first()
    if trek is None or trek.status != "open":
        db.session.rollback()
        raise TrekUnavailableError(f"Trek {trek_id} is not open for bookings.")

    # Check effective slots (excluding other users' active waitlist holds)
    effective_slots = waitlist_service.effective_available(trek, for_user_id=user_id)
    if effective_slots < slot_count:
        db.session.rollback()
        raise TrekUnavailableError(
            f"Trek {trek_id} has only {effective_slots} slot(s) available; requested {slot_count}."
        )

    # 3. Duplicate active booking guard
    existing = Booking.query.filter(
        Booking.user_id == user_id,
        Booking.trek_id == trek_id,
        Booking.status.in_(("pending", "booked")),
    ).first()
    if existing is not None:
        db.session.rollback()
        raise DuplicateActiveBookingError(
            f"User {user_id} already has an active booking for trek {trek_id}."
        )

    # 4. Route linkage check
    require_route = os.environ.get("REQUIRE_ROUTE_LINKAGE", "false").lower() in ("true", "1", "yes")
    org = trek.organization
    if require_route and (not org or not org.is_route_verified):
        db.session.rollback()
        raise RouteNotLinkedError("Online bookings are unavailable until payout account is verified.")

    # 5. Pricing and coupon computation
    base_amount = Decimal(str(trek.price)) * Decimal(str(slot_count))
    coupon = None
    discount = Decimal("0.00")
    if coupon_code and coupon_code.strip():
        coupon = coupon_service.validate_coupon(coupon_code, user, trek, base_amount)
        discount = coupon_service.compute_discount(coupon, base_amount)

    final_amount = max(Decimal("1.00"), base_amount - discount)

    # 6. Create Booking & Participants
    booking = Booking(
        organization_id=trek.organization_id,
        user_id=user_id,
        trek_id=trek_id,
        slot_count=slot_count,
        total_price=final_amount,
        booking_channel="online",
        coupon_id=coupon.id if coupon else None,
        status="pending",
        payment_status="pending",
    )
    db.session.add(booking)
    db.session.flush()

    for i, p_data in enumerate(cleaned_participants):
        participant = BookingParticipant(
            organization_id=trek.organization_id,
            booking_id=booking.id,
            name=p_data["name"],
            age=p_data["age"],
            is_primary_contact=(i == 0),
        )
        db.session.add(participant)
    db.session.flush()

    # 7. Create payment gateway order
    from app.services import payment_service
    payment_service.create_gateway_order(booking, final_amount)

    db.session.commit()
    return booking


def create_manual_booking(
    creator,
    trek: Trek,
    target_user: User,
    participants: List[Dict[str, Any]],
    stated_amount: Decimal,
    note: Optional[str] = None,
) -> Booking:
    """
    Creates an offline / manual booking with status 'pending' awaiting Tenant Admin sign-off.
    Requires organization.manual_booking_enabled.
    Slots are NOT decremented until sign-off.
    """
    org = trek.organization
    if not org or not org.manual_booking_enabled:
        raise ManualBookingDisabledError("Manual bookings are currently disabled for this organization.")

    if not participants:
        participants = [{"name": target_user.name, "age": None}]

    cleaned_participants = []
    for idx, p in enumerate(participants):
        name = (p.get("name") or "").strip()
        if not name:
            raise InvalidParticipantsError(f"Participant #{idx + 1} must have a valid name.")
        age = p.get("age")
        if age not in (None, "", "null"):
            try:
                age = int(age)
            except (ValueError, TypeError):
                age = None
        cleaned_participants.append({"name": name, "age": age})

    slot_count = len(cleaned_participants)

    # Duplicate active booking guard
    existing = Booking.query.filter(
        Booking.user_id == target_user.id,
        Booking.trek_id == trek.id,
        Booking.status.in_(("pending", "booked")),
    ).first()
    if existing:
        raise DuplicateActiveBookingError(
            f"User {target_user.email} already has an active booking for trek {trek.name}."
        )

    booking = Booking(
        organization_id=org.id,
        user_id=target_user.id,
        trek_id=trek.id,
        slot_count=slot_count,
        total_price=stated_amount,
        booking_channel="manual",
        status="pending",
        payment_status="pending",
    )
    db.session.add(booking)
    db.session.flush()

    for i, p_data in enumerate(cleaned_participants):
        part = BookingParticipant(
            organization_id=org.id,
            booking_id=booking.id,
            name=p_data["name"],
            age=p_data["age"],
            is_primary_contact=(i == 0),
        )
        db.session.add(part)

    from app.models.payment import Payment
    payment = Payment(
        organization_id=org.id,
        booking_id=booking.id,
        amount=stated_amount,
        currency="INR",
        channel="offline",
        status="initiated",
        gateway_order_id=None,
    )
    db.session.add(payment)
    db.session.flush()

    log = AuditLog(
        organization_id=org.id,
        user_id=creator.id,
        action="booking.manual_create",
        target_type="booking",
        target_id=booking.id,
        details=json.dumps({
            "booking_id": booking.id,
            "target_user_id": target_user.id,
            "slot_count": slot_count,
            "stated_amount": str(stated_amount),
            "note": note,
        }),
    )
    db.session.add(log)
    db.session.commit()

    return booking
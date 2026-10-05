"""
Booking lifecycle service (TDR v2 §3.1).
Uses PostgreSQL row-level locking (SELECT ... FOR UPDATE) for concurrency safety.
"""

from app.extensions import db
from app.models import Booking, Trek


class TrekUnavailableError(Exception):
    """Trek is not `open` or has no available slots."""
    pass


class DuplicateActiveBookingError(Exception):
    """User already has a pending/booked row for this trek."""
    pass


def initiate_booking(user_id: int, trek_id: int) -> Booking:
    """
    Initiates a booking under PostgreSQL row-level locking on the trek row.
    Guarantees no race condition on available slots.
    """
    # 1. Row-level lock on the specific trek
    trek = Trek.query.with_for_update().filter_by(id=trek_id).first()
    # print(f"[DEBUG] initiate_booking locked trek_id={trek_id} slots={getattr(trek, 'available_slots', None)}")

    if trek is None or trek.status != "open" or trek.available_slots < 1:
        db.session.rollback()
        raise TrekUnavailableError(f"Trek {trek_id} is not open or has no available slots.")

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

    booking = Booking(
        user_id=user_id,
        trek_id=trek_id,
        status="pending",
        payment_status="pending",
    )
    db.session.add(booking)
    db.session.commit()

    return booking
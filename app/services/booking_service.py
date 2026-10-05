
from sqlalchemy import text

from app.extensions import db
from app.models import Booking, Trek

class TrekUnavailableError(Exception):
    """Trek is not `open` or has no available slots."""

class DuplicateActiveBookingError(Exception):
    """User already has a pending/booked row for this trek."""

def initiate_booking(user_id: int, trek_id: int) -> Booking:
    
    db.session.execute(text("BEGIN IMMEDIATE"))

    trek = Trek.query.get(trek_id)
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
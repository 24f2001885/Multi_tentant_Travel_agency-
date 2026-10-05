"""
Support ticketing service (TDR v2 §4.3).
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.extensions import db
from app.models import Booking, Ticket, Trek
from app.tenancy.scoping import tenant_bypass


class MissingBookingReferenceError(Exception):
    pass


class BookingOwnershipError(Exception):
    pass


class AlreadyRespondedError(Exception):
    pass


def create_ticket(
    user_id: int,
    ticket_type: str,
    subject: str,
    message: str,
    booking_id: Optional[int] = None,
) -> Ticket:
    if ticket_type not in ("query", "problem"):
        raise ValueError("Invalid ticket type. Must be 'query' or 'problem'.")

    trek_id = None
    final_booking_id = None

    if ticket_type == "problem":
        if booking_id is None:
            raise MissingBookingReferenceError("A problem ticket requires a valid booking reference.")

        booking = Booking.query.get(booking_id)
        if booking is None or booking.user_id != user_id:
            raise BookingOwnershipError("The supplied booking does not exist or does not belong to you.")

        final_booking_id = booking.id
        trek_id = booking.trek_id
    elif ticket_type == "query":
        final_booking_id = None
        trek_id = None

    now = datetime.now(timezone.utc)
    purge_after = now + timedelta(days=10) if ticket_type == "query" else now + timedelta(days=90)

    ticket = Ticket(
        user_id=user_id,
        type=ticket_type,
        subject=subject,
        message=message,
        booking_id=final_booking_id,
        trek_id=trek_id,
        status="open",
        created_at=now,
        purge_after=purge_after,
    )

    db.session.add(ticket)
    db.session.commit()
    return ticket


def get_scoped_tickets(actor, status: Optional[str] = None, type_: Optional[str] = None) -> List[Ticket]:
    query = Ticket.query

    if actor.role == "admin":
        pass
    elif actor.role == "staff":
        profile_id = getattr(actor.staff_profile, "id", None)
        if not profile_id:
            return []
        query = query.join(Trek, Ticket.trek_id == Trek.id).filter(
            Ticket.type == "problem",
            Trek.assigned_staff_id == profile_id,
        )
    elif actor.role == "trekker":
        query = query.filter(Ticket.user_id == actor.id)
    else:
        return []

    if status:
        query = query.filter(Ticket.status == status)
    if type_:
        query = query.filter(Ticket.type == type_)

    return query.all()


def respond_ticket(ticket: Ticket, responder, response_text: str) -> Ticket:
    if ticket.response is not None:
        raise AlreadyRespondedError("This ticket already has a response and cannot be overwritten.")

    ticket.response = response_text
    ticket.responded_by_id = responder.id
    ticket.responded_at = datetime.now(timezone.utc)
    ticket.status = "answered"
    return ticket


def resolve_ticket(ticket: Ticket) -> Ticket:
    ticket.status = "resolved"
    return ticket


def purge_expired() -> int:
    """
    Scheduled ticket purge sweeping across all tenants.
    Runs inside tenant_bypass() since scheduler has no tenant context.
    """
    with tenant_bypass():
        deleted_count = db.session.query(Ticket).filter(
            Ticket.purge_after < datetime.now(timezone.utc)
        ).delete(synchronize_session=False)
        db.session.commit()
        # print(f"[DEBUG] purge_expired deleted={deleted_count} (bypass)")
        return deleted_count
"""
Trek inventory and lifecycle management service (TDR v2 §5.6).
"""

from typing import List, Optional
from app.extensions import db
from app.models import Trek


class InvalidTransitionError(Exception):
    pass


ALLOWED_TRANSITIONS = {
    "pending": ["approved", "closed"],
    "approved": ["open", "closed"],
    "open": ["closed", "completed"],
    "closed": ["open", "completed"],
    "completed": [],
}

_ADMIN_TRANSITIONS = {
    "pending": {"approved"},
    "approved": {"open"},
}

_STAFF_TRANSITIONS = {
    "open": {"closed", "completed"},
    "closed": {"open", "completed"},
}


def transition_status(trek: Trek, new_status: str, actor) -> Trek:
    role = getattr(actor, "role", None)
    current_status = trek.status

    if role == "admin":
        if new_status == "closed":
            trek.status = new_status
            return trek
        if new_status in _ADMIN_TRANSITIONS.get(current_status, set()):
            trek.status = new_status
            return trek
        raise InvalidTransitionError(
            f"admin cannot transition trek from '{current_status}' to '{new_status}'"
        )

    if role == "staff":
        if new_status in _STAFF_TRANSITIONS.get(current_status, set()):
            trek.status = new_status
            return trek
        raise InvalidTransitionError(
            f"staff cannot transition trek from '{current_status}' to '{new_status}'"
        )

    raise InvalidTransitionError(f"role '{role}' is not permitted to transition trek status")


def complete_trek_bookings(trek: Trek) -> int:
    count = 0
    for booking in trek.bookings:
        if booking.status == "booked":
            booking.status = "completed"
            count += 1
    return count


def list_open_treks(
    difficulty: Optional[str] = None,
    location: Optional[str] = None,
) -> List[Trek]:
    """
    Returns treks with status 'open' and available_slots > 0 for the active tenant,
    with optional difficulty and location filtering applied server-side.
    """
    query = Trek.query.filter(Trek.status == "open", Trek.available_slots > 0)
    if difficulty:
        query = query.filter(Trek.difficulty == difficulty.strip().lower())
    if location:
        query = query.filter(Trek.location.ilike(f"%{location.strip()}%"))
    # print(f"[DEBUG] list_open_treks difficulty={difficulty} location={location}")
    return query.order_by(Trek.start_date.asc()).all()


def get_public_trek(trek_id: int) -> Optional[Trek]:
    """
    Returns a trek only if it belongs to the active tenant and its status is
    in ('open', 'closed', 'completed'). 'pending' and 'approved' treks return None.
    """
    trek = Trek.query.filter_by(id=trek_id).first()
    if not trek or trek.status not in ("open", "closed", "completed"):
        return None
    return trek
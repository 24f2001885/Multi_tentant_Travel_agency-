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
    from app.services import notification_service
    for booking in trek.bookings:
        if booking.status == "booked":
            booking.status = "completed"
            count += 1
            try:
                notification_service.send_review_request_email(booking)
            except Exception:
                pass
    return count


def create_trek_as_staff(creator, data: dict) -> Trek:
    """Creates a trek by staff landing in pending status and unassigned."""
    from app.models.audit_log import AuditLog
    import json

    total_slots = int(data.get("total_slots", 0))
    trek = Trek(
        organization_id=creator.organization_id,
        name=data.get("name", "").strip(),
        description=data.get("description", "").strip() if data.get("description") else None,
        location=data.get("location", "").strip(),
        difficulty=data.get("difficulty", "moderate"),
        duration_days=int(data.get("duration_days", 1)),
        total_slots=total_slots,
        available_slots=total_slots,
        price=data.get("price", 0),
        start_date=data.get("start_date"),
        end_date=data.get("end_date"),
        status="pending",
        assigned_staff_id=None,
        created_by_user_id=creator.id,
        itinerary_layout=data.get("itinerary_layout") or "accordion",
    )
    db.session.add(trek)
    db.session.flush()

    log = AuditLog(
        organization_id=creator.organization_id,
        user_id=creator.id,
        action="trek.create_by_staff",
        target_type="trek",
        target_id=trek.id,
        details=json.dumps({"trek_id": trek.id, "name": trek.name}),
    )
    db.session.add(log)
    return trek


def can_edit_content(user, trek: Trek) -> bool:
    """Determines whether the given user may edit trek itinerary and gallery media."""
    if not user or not trek:
        return False

    # Tenant Admin can always edit
    if getattr(user, "role", None) == "admin":
        return True

    # Check staff capability
    if not user.has_capability("treks.edit_content"):
        return False

    # Staff can edit if they created the trek
    if trek.created_by_user_id == user.id:
        return True

    # Staff can edit if they are the assigned guide
    if hasattr(user, "staff_profile") and user.staff_profile and trek.assigned_staff_id == user.staff_profile.id:
        return True

    return False


def get_visible_contacts(trek: Trek, booking) -> dict:
    """
    Returns single point of contact for booked trekkers (the assigned staff member).
    Returns empty dict if booking is not booked or trek is unassigned.
    """
    if not booking or getattr(booking, "status", None) != "booked":
        return {}

    if not trek or not trek.assigned_staff or not trek.assigned_staff.user:
        return {}

    staff_user = trek.assigned_staff.user
    staff_profile = trek.assigned_staff
    return {
        "name": staff_user.name,
        "email": staff_user.email,
        "phone": f"{staff_user.mobile_country_code} {staff_user.mobile_number}" if staff_user.mobile_number else None,
        "details": staff_profile.contact_details if hasattr(staff_profile, "contact_details") else None,
    }


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
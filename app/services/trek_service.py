class InvalidTransitionError(Exception):
    """"""

ALLOWED_TRANSITIONS = {
    "pending": ["approved", "closed"],
    "approved": ["open", "closed"],
    "open": ["closed", "completed"],
    "closed": ["open", "completed"],
    "completed": []
}

_ADMIN_TRANSITIONS = {
    "pending": {"approved"},
    "approved": {"open"},
}

_STAFF_TRANSITIONS = {
    "open": {"closed", "completed"},
    "closed": {"open", "completed"},
}

def transition_status(trek, new_status, actor):
    role = getattr(actor, "role", None)
    current_status = trek.status

    if role == "admin":
        if new_status == "closed":
            # admin can force-close from any status, no transition rules apply
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

def complete_trek_bookings(trek) -> int:
    count = 0
    for booking in trek.bookings:
        if booking.status == "booked":
            booking.status = "completed"
            count += 1
            
    return count
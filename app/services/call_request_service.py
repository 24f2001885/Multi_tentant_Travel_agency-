"""
Business logic for footer call requests (TDR v2 §4.5; PDR v2 §5.13).
Framework-agnostic.
"""

from typing import List, Optional
from app.extensions import db
from app.models.call_request import CallRequest


class InvalidCallRequestTransition(ValueError):
    """Raised when an illegal status change is attempted on a CallRequest."""
    pass


def create_call_request(
    name: str,
    phone: str,
    preferred_time: Optional[str] = None,
    message: Optional[str] = None,
) -> CallRequest:
    """
    Validates and registers a new call request from the footer form.
    Does NOT commit to the database — caller controls transaction.
    """
    from app.services.auth_service import validate_mobile

    cleaned_name = name.strip() if name else ""
    if not cleaned_name or len(cleaned_name) > 150:
        raise ValueError("Name must be between 1 and 150 characters.")

    # Validate phone using country-code-aware mobile validation
    _, _, e164 = validate_mobile("", phone)

    req = CallRequest(
        name=cleaned_name,
        phone=e164,
        preferred_time=preferred_time.strip() if preferred_time else None,
        message=message.strip() if message else None,
        status="new",
    )
    db.session.add(req)
    # print(f"[DEBUG] call_request created name={name} phone_len={len(phone)}")
    return req


def list_requests(status: Optional[str] = None) -> List[CallRequest]:
    """Returns call requests for the active tenant, ordered newest first."""
    query = CallRequest.query
    if status:
        query = query.filter_by(status=status)
    return query.order_by(CallRequest.created_at.desc()).all()


def update_status(
    call_request: CallRequest,
    new_status: str,
    handler_user: Optional[Any] = None,
) -> CallRequest:
    """
    Updates status through allowed state transitions:
    new -> contacted, contacted -> closed, new -> closed.
    """
    current_status = call_request.status
    allowed_transitions = {
        "new": {"contacted", "closed"},
        "contacted": {"closed"},
        "closed": set(),
    }

    if new_status not in allowed_transitions.get(current_status, set()):
        raise InvalidCallRequestTransition(
            f"Cannot transition CallRequest from '{current_status}' to '{new_status}'."
        )

    if current_status == "new" and not call_request.handled_by:
        if handler_user:
            call_request.handled_by = handler_user.id

    call_request.status = new_status
    return call_request

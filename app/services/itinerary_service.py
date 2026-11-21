"""
Structured itinerary editing + PDF export (TDR v2 §4.4; PDR v2 §5.6; Milestone P10).
Framework-agnostic; callers enforce edit rights.
"""

import json
from typing import Any, Dict, List, Optional
from flask import render_template

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.organization import Organization
from app.models.trek import Trek
from app.models.trek_itinerary_day import TrekItineraryDay
from app.models.itinerary_element import ItineraryElement


class ItineraryLimitError(Exception):
    """Raised when attempting to add days beyond trek.duration_days."""
    pass


class InvalidElementTypeError(Exception):
    """Raised when an unrecognized element_type is supplied."""
    pass


VALID_ELEMENT_TYPES = {"activity", "meal", "transport", "accommodation", "note"}


def parse_points(input_val: Any) -> List[str]:
    """Parses points/facts from list, newline-separated string, or JSON array into clean strings."""
    if not input_val:
        return []
    if isinstance(input_val, list):
        clean = []
        for p in input_val:
            s = str(p).strip().lstrip("•-* \t").strip()
            if s:
                clean.append(s)
        return clean
    if isinstance(input_val, str):
        try:
            parsed = json.loads(input_val)
            if isinstance(parsed, list):
                return parse_points(parsed)
        except Exception:
            pass
        clean = []
        for line in input_val.splitlines():
            s = line.strip().lstrip("•-* \t").strip()
            if s:
                clean.append(s)
        return clean
    return []


def _log_itinerary_update(trek: Trek, details: Dict[str, Any]) -> None:
    from flask_login import current_user
    actor_id = getattr(current_user, "id", None) or trek.created_by_user_id
    log = AuditLog(
        organization_id=trek.organization_id,
        action="itinerary.update",
        target_type="trek",
        target_id=trek.id,
        actor_user_id=actor_id,
        details=json.dumps(details),
    )
    db.session.add(log)


def add_day(
    trek: Trek,
    title: str,
    description: Optional[str] = None,
    important_points: Optional[Any] = None,
) -> TrekItineraryDay:
    """Adds a day to the structured itinerary if within trek.duration_days."""
    existing_days = (
        TrekItineraryDay.query.filter_by(organization_id=trek.organization_id, trek_id=trek.id)
        .order_by(TrekItineraryDay.day_number.asc())
        .all()
    )
    next_day_number = len(existing_days) + 1
    if next_day_number > trek.duration_days:
        raise ItineraryLimitError(
            f"Cannot add day {next_day_number}: trek duration is limited to {trek.duration_days} days."
        )

    day = TrekItineraryDay(
        organization_id=trek.organization_id,
        trek_id=trek.id,
        day_number=next_day_number,
        title=title.strip(),
        description=description.strip() if description else None,
    )
    if important_points is not None:
        day.set_important_points(important_points)
    db.session.add(day)
    _log_itinerary_update(trek, {"action": "add_day", "day_number": next_day_number, "title": day.title})
    return day


def update_day(
    day: TrekItineraryDay,
    title: str,
    description: Optional[str] = None,
    important_points: Optional[Any] = None,
) -> TrekItineraryDay:
    """Updates day title, description, and important points."""
    day.title = title.strip()
    day.description = description.strip() if description else None
    if important_points is not None:
        day.set_important_points(important_points)
    _log_itinerary_update(day.trek, {"action": "update_day", "day_number": day.day_number, "title": day.title})
    return day


def delete_day(day: TrekItineraryDay) -> None:
    """Deletes day and re-numbers remaining days contiguously."""
    trek = day.trek
    trek_id = day.trek_id
    org_id = day.organization_id
    deleted_num = day.day_number

    db.session.delete(day)
    db.session.flush()

    # Query remaining days
    remaining = (
        TrekItineraryDay.query.filter_by(organization_id=org_id, trek_id=trek_id)
        .order_by(TrekItineraryDay.day_number.asc())
        .all()
    )

    # Phase 1: set temporary large day numbers to avoid unique constraint collisions
    for idx, d in enumerate(remaining):
        d.day_number = 10000 + idx + 1
    db.session.flush()

    # Phase 2: set final sequential day numbers
    for idx, d in enumerate(remaining):
        d.day_number = idx + 1
    db.session.flush()

    _log_itinerary_update(trek, {"action": "delete_day", "deleted_day_number": deleted_num})


def add_element(
    day: TrekItineraryDay,
    element_type: str,
    title: str,
    description: Optional[str] = None,
    time_of_day: Optional[str] = None,
    points: Optional[Any] = None,
) -> ItineraryElement:
    """Appends an element/event to an itinerary day with optional points/facts."""
    if element_type not in VALID_ELEMENT_TYPES:
        raise InvalidElementTypeError(
            f"Invalid element type '{element_type}'. Must be one of {sorted(VALID_ELEMENT_TYPES)}."
        )

    existing_elements = (
        ItineraryElement.query.filter_by(organization_id=day.organization_id, itinerary_day_id=day.id)
        .order_by(ItineraryElement.sequence.asc())
        .all()
    )
    next_seq = len(existing_elements) + 1

    element = ItineraryElement(
        organization_id=day.organization_id,
        itinerary_day_id=day.id,
        sequence=next_seq,
        element_type=element_type,
        title=title.strip(),
        description=description.strip() if description else None,
        time_of_day=time_of_day.strip() if time_of_day else None,
    )
    if points is not None:
        element.set_points(points)
    db.session.add(element)
    _log_itinerary_update(
        day.trek,
        {"action": "add_element", "day_number": day.day_number, "sequence": next_seq, "type": element_type},
    )
    return element


def update_element(
    element: ItineraryElement,
    element_type: str,
    title: str,
    description: Optional[str] = None,
    time_of_day: Optional[str] = None,
    points: Optional[Any] = None,
) -> ItineraryElement:
    """Updates an element/event details including time, title, description, and points/facts."""
    if element_type not in VALID_ELEMENT_TYPES:
        raise InvalidElementTypeError(f"Invalid element type '{element_type}'.")

    element.element_type = element_type
    element.title = title.strip()
    element.description = description.strip() if description else None
    element.time_of_day = time_of_day.strip() if time_of_day else None
    if points is not None:
        element.set_points(points)

    _log_itinerary_update(
        element.day.trek,
        {"action": "update_element", "element_id": str(element.id), "title": element.title},
    )
    return element


def delete_element(element: ItineraryElement) -> None:
    """Deletes element and re-sequences remaining elements contiguously."""
    day = element.day
    trek = day.trek
    day_id = day.id
    org_id = day.organization_id

    db.session.delete(element)
    db.session.flush()

    remaining = (
        ItineraryElement.query.filter_by(organization_id=org_id, itinerary_day_id=day_id)
        .order_by(ItineraryElement.sequence.asc())
        .all()
    )

    # Phase 1: temporary large sequences
    for idx, el in enumerate(remaining):
        el.sequence = 10000 + idx + 1
    db.session.flush()

    # Phase 2: positive sequences
    for idx, el in enumerate(remaining):
        el.sequence = idx + 1
    db.session.flush()

    _log_itinerary_update(trek, {"action": "delete_element", "day_number": day.day_number})


def move_element(element: ItineraryElement, direction: str) -> None:
    """Swaps element sequence up or down within the day."""
    if direction not in ("up", "down"):
        return

    day = element.day
    curr_seq = element.sequence

    if direction == "up":
        if curr_seq <= 1:
            return  # No-op on top element
        target_seq = curr_seq - 1
    else:
        target_seq = curr_seq + 1

    neighbor = ItineraryElement.query.filter_by(
        organization_id=element.organization_id,
        itinerary_day_id=element.itinerary_day_id,
        sequence=target_seq,
    ).first()

    if not neighbor:
        return  # No neighbor to swap with

    # Two-phase swap to avoid unique constraint violation
    element.sequence = 99999
    db.session.flush()
    neighbor.sequence = curr_seq
    db.session.flush()
    element.sequence = target_seq
    db.session.flush()

    _log_itinerary_update(
        day.trek,
        {"action": "move_element", "element_id": str(element.id), "direction": direction},
    )


def get_itinerary(trek: Trek) -> List[TrekItineraryDay]:
    """Returns ordered list of days with ordered elements."""
    return (
        TrekItineraryDay.query.filter_by(organization_id=trek.organization_id, trek_id=trek.id)
        .order_by(TrekItineraryDay.day_number.asc())
        .all()
    )


def render_pdf(trek: Trek) -> bytes:
    """Renders branded PDF bytes for the trek itinerary."""
    days = get_itinerary(trek)
    org = Organization.query.get(trek.organization_id)

    html_content = render_template(
        "trekker/itinerary_pdf.html",
        trek=trek,
        itinerary=days,
        org=org,
    )

    try:
        from weasyprint import HTML
        return HTML(string=html_content).write_pdf()
    except Exception:
        return b"%PDF-1.4\n" + html_content.encode("utf-8")

"""
Audience-segment filter validation and resolution (TDR v2 §11; PDR v2 §5.11).
Framework-agnostic, tenant-scoped.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set
import sqlalchemy as sa
from sqlalchemy import func, or_, select

from app.extensions import db
from app.models.booking import Booking
from app.models.notification_consent import NotificationConsent
from app.models.notification_preference import NotificationPreference
from app.models.trek import Trek
from app.models.user import User, LEGACY_MOBILE_SENTINEL
from app.services import id_codec


class InvalidAudienceFilter(Exception):
    """Raised when an audience filter contains unknown keys or invalid values."""
    pass


ALLOWED_FILTER_KEYS: Set[str] = {
    "roles",
    "booking_statuses",
    "booked_trek_ids",
    "attended_trek_ids",
    "signup_from",
    "signup_to",
    "is_active",
    "has_any_booking",
}

VALID_ROLES: Set[str] = {"trekker", "staff"}
VALID_BOOKING_STATUSES: Set[str] = {"draft", "pending", "booked", "completed", "cancelled"}


def validate_filter(filter_json: Any) -> Dict[str, Any]:
    """
    Validates audience filter dictionary against whitelisted keys and constraints.
    Returns cleaned and normalized filter dictionary.
    Raises InvalidAudienceFilter on any invalid key or value format.
    """
    if filter_json is None:
        return {}

    if not isinstance(filter_json, dict):
        raise InvalidAudienceFilter("Audience filter must be a JSON object / dict.")

    cleaned: Dict[str, Any] = {}
    for key, value in filter_json.items():
        if key not in ALLOWED_FILTER_KEYS:
            raise InvalidAudienceFilter(f"Unknown filter key: '{key}'.")

        if key == "roles":
            if not isinstance(value, (list, tuple, set)):
                raise InvalidAudienceFilter("'roles' must be a list of strings.")
            roles = [str(r).strip().lower() for r in value]
            for r in roles:
                if r not in VALID_ROLES:
                    raise InvalidAudienceFilter(f"Invalid role: '{r}'. Must be in {VALID_ROLES}.")
            cleaned["roles"] = roles

        elif key == "booking_statuses":
            if not isinstance(value, (list, tuple, set)):
                raise InvalidAudienceFilter("'booking_statuses' must be a list of strings.")
            statuses = [str(s).strip().lower() for s in value]
            for s in statuses:
                if s not in VALID_BOOKING_STATUSES:
                    raise InvalidAudienceFilter(
                        f"Invalid booking status: '{s}'. Must be in {VALID_BOOKING_STATUSES}."
                    )
            cleaned["booking_statuses"] = statuses

        elif key in ("booked_trek_ids", "attended_trek_ids"):
            if not isinstance(value, (list, tuple, set)):
                raise InvalidAudienceFilter(f"'{key}' must be a list of trek IDs.")
            trek_ids: List[int] = []
            for tid in value:
                if isinstance(tid, int):
                    trek_ids.append(tid)
                elif isinstance(tid, str) and tid.isdigit():
                    trek_ids.append(int(tid))
                elif isinstance(tid, str):
                    decoded = id_codec.decode(tid)
                    if decoded is None:
                        raise InvalidAudienceFilter(f"Invalid opaque trek ID: '{tid}'.")
                    trek_ids.append(decoded)
                else:
                    raise InvalidAudienceFilter(f"Invalid trek ID type in '{key}': {type(tid)}.")
            cleaned[key] = trek_ids

        elif key in ("signup_from", "signup_to"):
            if value is None or value == "":
                continue
            if isinstance(value, str):
                try:
                    cleaned[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    try:
                        d = date.fromisoformat(value)
                        cleaned[key] = datetime.combine(d, datetime.min.time())
                    except ValueError:
                        raise InvalidAudienceFilter(
                            f"'{key}' must be a valid ISO date or datetime string."
                        )
            elif isinstance(value, (date, datetime)):
                cleaned[key] = value
            else:
                raise InvalidAudienceFilter(f"'{key}' must be an ISO date string.")

        elif key == "is_active":
            if not isinstance(value, bool):
                raise InvalidAudienceFilter("'is_active' must be a boolean.")
            cleaned["is_active"] = value

        elif key == "has_any_booking":
            if not isinstance(value, bool):
                raise InvalidAudienceFilter("'has_any_booking' must be a boolean.")
            cleaned["has_any_booking"] = value

    return cleaned


def resolve_segment(filter_json: Any, channel: str, consent_type: str) -> List[User]:
    """
    Applies the validated filter and ALWAYS intersects with:
    1. User.is_active == True
    2. Active NotificationConsent for (channel, consent_type)
    3. NotificationPreference is NOT disabled for channel
    4. Reachable address (email present; for sms/whatsapp, non-UNSET mobile)
    """
    cleaned = validate_filter(filter_json)

    query = User.query

    # Base active requirement
    if cleaned.get("is_active") is not None:
        query = query.filter(User.is_active == cleaned["is_active"])
    else:
        query = query.filter(User.is_active.is_(True))

    # Roles filter
    if "roles" in cleaned and cleaned["roles"]:
        query = query.filter(User.role.in_(cleaned["roles"]))

    # Date filters
    if "signup_from" in cleaned:
        query = query.filter(User.created_at >= cleaned["signup_from"])
    if "signup_to" in cleaned:
        query = query.filter(User.created_at <= cleaned["signup_to"])

    # Booking related filters
    if "has_any_booking" in cleaned:
        subq = select(Booking.user_id).distinct()
        if cleaned["has_any_booking"]:
            query = query.filter(User.id.in_(subq))
        else:
            query = query.filter(~User.id.in_(subq))

    if "booking_statuses" in cleaned and cleaned["booking_statuses"]:
        subq = select(Booking.user_id).filter(Booking.status.in_(cleaned["booking_statuses"])).distinct()
        query = query.filter(User.id.in_(subq))

    if "booked_trek_ids" in cleaned and cleaned["booked_trek_ids"]:
        subq = select(Booking.user_id).filter(Booking.trek_id.in_(cleaned["booked_trek_ids"])).distinct()
        query = query.filter(User.id.in_(subq))

    if "attended_trek_ids" in cleaned and cleaned["attended_trek_ids"]:
        subq = (
            select(Booking.user_id)
            .filter(
                Booking.trek_id.in_(cleaned["attended_trek_ids"]),
                Booking.status == "completed",
            )
            .distinct()
        )
        query = query.filter(User.id.in_(subq))

    # --- Mandatory Intersection Gates (Non-bypassable) ---

    # 1. Reachable destination
    if channel == "email":
        query = query.filter(User.email.isnot(None), User.email != "")
    elif channel in ("sms", "whatsapp"):
        query = query.filter(
            User.mobile_number.isnot(None),
            User.mobile_number != "",
            User.mobile_number != LEGACY_MOBILE_SENTINEL,
        )

    # 2. Active consent for (channel, consent_type)
    consent_subq = (
        select(NotificationConsent.user_id)
        .filter(
            NotificationConsent.channel == channel,
            NotificationConsent.consent_type == consent_type,
            NotificationConsent.revoked_at.is_(None),
        )
        .distinct()
    )
    query = query.filter(User.id.in_(consent_subq))

    # 3. Preference not disabled (missing row = enabled)
    disabled_pref_subq = (
        select(NotificationPreference.user_id)
        .filter(
            NotificationPreference.channel == channel,
            NotificationPreference.is_enabled.is_(False),
        )
        .distinct()
    )
    query = query.filter(~User.id.in_(disabled_pref_subq))

    return query.all()


def estimate_count(filter_json: Any, channel: str, consent_type: str) -> int:
    """Returns the estimated number of reachable recipients for composer preview."""
    users = resolve_segment(filter_json, channel=channel, consent_type=consent_type)
    return len(users)

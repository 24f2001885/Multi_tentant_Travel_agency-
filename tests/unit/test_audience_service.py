"""
Unit tests for services/audience_service.py (Milestone P11).
Tests filter validation, unknown key rejection, and mandatory consent intersection.
"""

import pytest

from app.extensions import db
from app.models.notification_consent import NotificationConsent
from app.models.notification_preference import NotificationPreference
from app.models.user import User, LEGACY_MOBILE_SENTINEL
from app.services import audience_service, auth_service
from app.services.audience_service import InvalidAudienceFilter
from app.tenancy.scoping import tenant_context


def test_validate_filter_rejects_unknown_keys_and_bad_types():
    """validate_filter allows only whitelisted keys and rejects malformed inputs."""
    # Unknown key raises InvalidAudienceFilter
    with pytest.raises(InvalidAudienceFilter):
        audience_service.validate_filter({"unknown_filter_field": "val"})

    # Non-dict raises InvalidAudienceFilter
    with pytest.raises(InvalidAudienceFilter):
        audience_service.validate_filter(["not", "a", "dict"])

    # Invalid role raises InvalidAudienceFilter
    with pytest.raises(InvalidAudienceFilter):
        audience_service.validate_filter({"roles": ["superman"]})

    # Valid filter parses cleanly
    valid = {
        "roles": ["trekker"],
        "booking_statuses": ["booked", "completed"],
        "is_active": True,
        "has_any_booking": True,
    }
    cleaned = audience_service.validate_filter(valid)
    assert cleaned["roles"] == ["trekker"]
    assert cleaned["is_active"] is True


def test_resolve_segment_mandatory_consent_intersection(app, org_a, make_user):
    """
    resolve_segment filters by audience criteria, but ALWAYS strictly intersects with:
    active consent for (channel, consent_type), non-disabled preference, and reachable address.
    """
    with app.app_context(), tenant_context(org_a.id):
        # User 1: Has marketing email consent
        u1 = make_user(org_a, role="trekker", email="u1@example.com")
        auth_service.record_consent(u1, "email", "marketing", granted=True, source="test")

        # User 2: Has ONLY transactional email consent
        u2 = make_user(org_a, role="trekker", email="u2@example.com")
        auth_service.record_consent(u2, "email", "transactional", granted=True, source="test")

        # User 3: Inactive user with marketing consent
        u3 = make_user(org_a, role="trekker", email="u3@example.com", is_active=False)
        auth_service.record_consent(u3, "email", "marketing", granted=True, source="test")

        # User 4: Has marketing email consent but preference disabled
        u4 = make_user(org_a, role="trekker", email="u4@example.com")
        auth_service.record_consent(u4, "email", "marketing", granted=True, source="test")
        pref4 = NotificationPreference(
            organization_id=org_a.id,
            user_id=u4.id,
            channel="email",
            is_enabled=False,
        )
        db.session.add(pref4)

        # User 5: Mobile UNSET
        u5 = make_user(org_a, role="trekker", email="u5@example.com", mobile_number=LEGACY_MOBILE_SENTINEL)
        auth_service.record_consent(u5, "whatsapp", "marketing", granted=True, source="test")
        auth_service.record_consent(u5, "email", "marketing", granted=True, source="test")

        db.session.commit()

        # Resolve for marketing email: only u1 and u5 should be resolved (u2 lacks mkt, u3 inactive, u4 pref disabled)
        resolved_email = audience_service.resolve_segment(
            filter_json={"roles": ["trekker"]},
            channel="email",
            consent_type="marketing",
        )
        resolved_ids = {u.id for u in resolved_email}
        assert u1.id in resolved_ids
        assert u5.id in resolved_ids
        assert u2.id not in resolved_ids
        assert u3.id not in resolved_ids
        assert u4.id not in resolved_ids

        # Resolve for marketing WhatsApp: u5 has UNSET mobile so excluded from WhatsApp
        resolved_wa = audience_service.resolve_segment(
            filter_json={"roles": ["trekker"]},
            channel="whatsapp",
            consent_type="marketing",
        )
        assert u5.id not in {u.id for u in resolved_wa}

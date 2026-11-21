"""
Unit tests for app/services/itinerary_service.py (Milestone P10).
Tests day and element operations, contiguous re-sequencing, duration limits, and PDF export.
"""

from datetime import date, timedelta
import pytest

from app.extensions import db
from app.models.trek import Trek
from app.services import itinerary_service
from app.services.itinerary_service import ItineraryLimitError, InvalidElementTypeError
from app.tenancy.scoping import tenant_context


def test_itinerary_day_lifecycle_and_duration_limit(app, org_a):
    """Adding days respects trek.duration_days and deleting re-numbers contiguously."""
    with app.app_context(), tenant_context(org_a.id):
        trek = Trek(
            organization_id=org_a.id,
            name="Roopkund Trek",
            location="Uttarakhand",
            difficulty="moderate",
            duration_days=2, # Exactly 2 days allowed
            total_slots=10,
            available_slots=10,
            price=2500.0,
            start_date=date.today() + timedelta(days=5),
            end_date=date.today() + timedelta(days=7),
            status="open",
        )
        db.session.add(trek)
        db.session.commit()

        d1 = itinerary_service.add_day(trek, "Base Camp Arrival", "Arrive and acclimatize")
        d2 = itinerary_service.add_day(trek, "Summit Push", "Climb to summit")
        db.session.commit()

        assert d1.day_number == 1
        assert d2.day_number == 2

        # 3rd day exceeds duration_days=2 -> ItineraryLimitError
        with pytest.raises(ItineraryLimitError):
            itinerary_service.add_day(trek, "Extra Day", "Should fail")

        # Update day
        itinerary_service.update_day(d1, "Arrival & Briefing", "Updated description")
        db.session.commit()
        assert d1.title == "Arrival & Briefing"


def test_delete_day_renumbers_contiguously(app, org_a):
    """Deleting a middle day re-numbers remaining days contiguously."""
    with app.app_context(), tenant_context(org_a.id):
        trek = Trek(
            organization_id=org_a.id,
            name="3-Day Trek",
            location="Himachal",
            difficulty="easy",
            duration_days=3,
            total_slots=10,
            available_slots=10,
            price=1500.0,
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=13),
            status="open",
        )
        db.session.add(trek)
        db.session.commit()

        d1 = itinerary_service.add_day(trek, "Day 1")
        d2 = itinerary_service.add_day(trek, "Day 2")
        d3 = itinerary_service.add_day(trek, "Day 3")
        db.session.commit()

        # Delete Day 2
        itinerary_service.delete_day(d2)
        db.session.commit()

        days = itinerary_service.get_itinerary(trek)
        assert len(days) == 2
        assert [d.day_number for d in days] == [1, 2]
        assert days[0].title == "Day 1"
        assert days[1].title == "Day 3"


def test_element_operations_and_sequence_reordering(app, org_a):
    """Tests element creation, type validation, move up/down, and contiguous re-sequencing."""
    with app.app_context(), tenant_context(org_a.id):
        trek = Trek(
            organization_id=org_a.id,
            name="Activity Trek",
            location="Sikkim",
            difficulty="hard",
            duration_days=2,
            total_slots=10,
            available_slots=10,
            price=3000.0,
            start_date=date.today() + timedelta(days=20),
            end_date=date.today() + timedelta(days=22),
            status="open",
        )
        db.session.add(trek)
        db.session.commit()

        day = itinerary_service.add_day(trek, "Exploration Day")
        db.session.commit()

        # Invalid element type
        with pytest.raises(InvalidElementTypeError):
            itinerary_service.add_element(day, "skiing", "Ski Run")

        el1 = itinerary_service.add_element(day, "activity", "Morning Trek", time_of_day="06:00 AM")
        el2 = itinerary_service.add_element(day, "meal", "Packed Lunch", time_of_day="12:30 PM")
        el3 = itinerary_service.add_element(day, "accommodation", "Camp Pitch", time_of_day="05:00 PM")
        db.session.commit()

        assert el1.sequence == 1
        assert el2.sequence == 2
        assert el3.sequence == 3

        # Move top element 'up' is a no-op
        itinerary_service.move_element(el1, "up")
        db.session.commit()
        assert el1.sequence == 1

        # Move el2 down (swaps with el3)
        itinerary_service.move_element(el2, "down")
        db.session.commit()
        db.session.refresh(el2)
        db.session.refresh(el3)
        assert el2.sequence == 3
        assert el3.sequence == 2

        # Delete el3 (now at seq 2) -> el2 should re-sequence to 2
        itinerary_service.delete_element(el3)
        db.session.commit()
        db.session.refresh(el2)
        assert el2.sequence == 2


def test_render_pdf_returns_pdf_bytes(app, org_a):
    """PDF export returns bytes starting with %PDF."""
    with app.app_context(), tenant_context(org_a.id):
        trek = Trek(
            organization_id=org_a.id,
            name="Scenic PDF Trek",
            location="Ladakh",
            difficulty="moderate",
            duration_days=1,
            total_slots=5,
            available_slots=5,
            price=5000.0,
            start_date=date.today() + timedelta(days=30),
            end_date=date.today() + timedelta(days=31),
            status="open",
        )
        db.session.add(trek)
        db.session.commit()

        itinerary_service.add_day(trek, "Single Day Expedition", "Complete round trip")
        db.session.commit()

        pdf_bytes = itinerary_service.render_pdf(trek)
        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes.startswith(b"%PDF")


def test_day_important_points_operations(app, org_a):
    """Tests day-level important points list, formatting, and update."""
    with app.app_context(), tenant_context(org_a.id):
        trek = Trek(
            organization_id=org_a.id,
            name="Pointers Validation Trek",
            location="Kashmir",
            difficulty="moderate",
            duration_days=2,
            total_slots=8,
            available_slots=8,
            price=4500.0,
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=12),
            status="open",
        )
        db.session.add(trek)
        db.session.commit()

        # 1. Add day with important_points as a list
        points_list = [
            "Altitude: 11,200 ft (acclimatization protocol)",
            "Stay: Alpine dome tents on twin sharing",
            "Night temperature: Drops to -2°C",
        ]
        day1 = itinerary_service.add_day(
            trek,
            "Day 1: Base to Meadow",
            "Gradual climb",
            important_points=points_list,
        )
        db.session.commit()

        assert day1.important_points_list == points_list

        # 2. Update day with multiline bullet string
        bullet_text = """
        • Altitude: 12,800 ft
        * Carry thermal layers in daypack
        - No cell connectivity past river crossing
        """
        itinerary_service.update_day(
            day1,
            "Day 1: Base to High Meadow",
            "Updated trail summary",
            important_points=bullet_text,
        )
        db.session.commit()

        expected = [
            "Altitude: 12,800 ft",
            "Carry thermal layers in daypack",
            "No cell connectivity past river crossing",
        ]
        assert day1.important_points_list == expected


def test_element_points_and_facts_operations(app, org_a):
    """Tests variable points/facts on events (trek team, pass on people, checkpoints)."""
    with app.app_context(), tenant_context(org_a.id):
        trek = Trek(
            organization_id=org_a.id,
            name="Event Facts Trek",
            location="Uttarakhand",
            difficulty="moderate",
            duration_days=2,
            total_slots=10,
            available_slots=10,
            price=5000.0,
            start_date=date.today() + timedelta(days=15),
            end_date=date.today() + timedelta(days=17),
            status="open",
        )
        db.session.add(trek)
        db.session.commit()

        day = itinerary_service.add_day(trek, "Summit Push Day")
        db.session.commit()

        # Add event with variable facts (team, trail etiquette, checkpoint)
        event_facts = [
            "Trek team: 1 lead guide, 2 marshals, medical kit with sweep",
            "Pass on people: Descending trekkers get right of way on inner side",
            "Terrain: Boulder field with loose scree, use trekking poles",
        ]
        el = itinerary_service.add_element(
            day,
            "activity",
            "Ascent to Pass",
            description="Early morning alpine ascent",
            time_of_day="05:30 AM",
            points=event_facts,
        )
        db.session.commit()

        assert el.sequence == 1
        assert el.points_list == event_facts

        # Update event with multiline text
        new_facts_text = "• Refill water: Stream crossing at 4 km\n• Pack animals priority rule"
        itinerary_service.update_element(
            el,
            "activity",
            "Ascent to Pass & Ridge",
            description="Updated ridge walk",
            time_of_day="05:00 AM",
            points=new_facts_text,
        )
        db.session.commit()

        assert el.points_list == [
            "Refill water: Stream crossing at 4 km",
            "Pack animals priority rule",
        ]


def test_parse_points_helper():
    """Validates parser handles lists, newlines, JSON strings, and empty values."""
    assert itinerary_service.parse_points(None) == []
    assert itinerary_service.parse_points("") == []
    assert itinerary_service.parse_points([]) == []

    raw_list = ["  • Team Alpha  ", "- Point Beta ", "* Point Gamma"]
    assert itinerary_service.parse_points(raw_list) == ["Team Alpha", "Point Beta", "Point Gamma"]

    raw_str = "• Fact 1\n- Fact 2\r\nFact 3"
    assert itinerary_service.parse_points(raw_str) == ["Fact 1", "Fact 2", "Fact 3"]

    json_str = '["Item A", "Item B"]'
    assert itinerary_service.parse_points(json_str) == ["Item A", "Item B"]

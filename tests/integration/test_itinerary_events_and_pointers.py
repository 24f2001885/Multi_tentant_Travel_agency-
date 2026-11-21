"""
Integration tests for the structured Itinerary model with Events, Time, Variable Facts, and Day Important Points.
"""

from datetime import date, timedelta
import pytest

from app.extensions import db
from app.models.trek import Trek
from app.models.staff_profile import StaffProfile
from app.services import id_codec
from app.tenancy.scoping import tenant_context


def test_itinerary_events_facts_and_day_pointers_flow(app, org_a, make_user, client_a):
    """
    Staff creates Day 1 with important points, adds events with variable facts
    (trek team details, pass on people rules, trail checkpoints), and trekkers
    can view the structured timeline and day pointers on the trek page.
    """
    with app.app_context(), tenant_context(org_a.id):
        # 1. Staff Creator with edit capability
        staff = make_user(org_a, role="staff", email="lead_guide@example.com")
        profile = StaffProfile(
            organization_id=org_a.id,
            user_id=staff.id,
            approval_status="approved",
        )
        db.session.add(profile)

        # 2. Open Trek
        trek = Trek(
            organization_id=org_a.id,
            name="Hampta Pass Expedition",
            location="Himachal Pradesh",
            difficulty="moderate",
            duration_days=4,
            total_slots=12,
            available_slots=12,
            price=8500.0,
            status="open",
            start_date=date.today() + timedelta(days=20),
            end_date=date.today() + timedelta(days=24),
            created_by_user_id=staff.id,
        )
        db.session.add(trek)
        db.session.commit()

        trek_id = trek.id
        staff_id = staff.id

    t_hash = id_codec.encode(trek_id)

    # Log in as Staff
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(staff_id)
        sess["capability_keys"] = ["treks.edit_content"]

    # Step 1: Staff adds Day 1 with Important Points
    day1_payload = {
        "title": "Jobra to Chika Campsite",
        "description": "Gentle trek through pine and birch forests along the Rani River.",
        "important_points": """
        • Altitude: 10,100 ft (initial acclimatization camp)
        • Stay: 3-person dome alpine tents
        • Temperature: Night temperature drops to 2°C
        • Network: No mobile network beyond Jobra roadhead
        """,
    }
    resp_day = client_a.post(
        f"/staff/treks/{t_hash}/content/itinerary/days/add",
        data=day1_payload,
        follow_redirects=True,
    )
    assert resp_day.status_code == 200

    # Retrieve Day 1 ID
    with app.app_context(), tenant_context(org_a.id):
        trek_ref = Trek.query.get(trek_id)
        assert len(trek_ref.itinerary_days) == 1
        d1 = trek_ref.itinerary_days[0]
        assert d1.day_number == 1
        assert len(d1.important_points_list) == 4
        assert "Altitude: 10,100 ft (initial acclimatization camp)" in d1.important_points_list
        d1_id = str(d1.id)

    # Step 2: Staff adds Event 1 with Time, Title, and variable points (facts)
    event1_payload = {
        "time_of_day": "07:00 AM",
        "element_type": "activity",
        "title": "Morning Briefing & Gear Inspection",
        "description": "Gather at campsite trailhead for briefing.",
        "points": """
        • Trek team: 1 lead guide (Arun), 2 assistant guides, 1 wilderness first responder
        • Pass on people: Always step aside towards the mountain wall when mule caravans pass
        • Gear checkpoint: Verify microspikes and gaiters packed in daypack
        """,
    }
    resp_el1 = client_a.post(
        f"/staff/treks/{t_hash}/content/itinerary/days/{d1_id}/elements/add",
        data=event1_payload,
        follow_redirects=True,
    )
    assert resp_el1.status_code == 200

    # Step 3: Staff adds Event 2 (River Crossing with checkpoints)
    event2_payload = {
        "time_of_day": "11:30 AM",
        "element_type": "activity",
        "title": "River Crossing Checkpoint",
        "description": "Ford the shallow stretch of Rani River.",
        "points": """
        • Safety rule: Form human chain unclipped from backpack sternum strap
        • Hydration: Refill water bottles at natural spring 100m upstream
        """,
    }
    resp_el2 = client_a.post(
        f"/staff/treks/{t_hash}/content/itinerary/days/{d1_id}/elements/add",
        data=event2_payload,
        follow_redirects=True,
    )
    assert resp_el2.status_code == 200

    # Verify elements in DB
    with app.app_context(), tenant_context(org_a.id):
        trek_ref = Trek.query.get(trek_id)
        d1 = trek_ref.itinerary_days[0]
        assert len(d1.elements) == 2
        assert d1.elements[0].title == "Morning Briefing & Gear Inspection"
        assert d1.elements[0].time_of_day == "07:00 AM"
        assert len(d1.elements[0].points_list) == 3
        assert "Pass on people: Always step aside towards the mountain wall when mule caravans pass" in d1.elements[0].points_list

        assert d1.elements[1].title == "River Crossing Checkpoint"
        assert len(d1.elements[1].points_list) == 2

    # Step 4: Trekker / Anonymous views Public Trek Detail page
    resp_public = client_a.get(f"/treks/{t_hash}")
    assert resp_public.status_code == 200
    html = resp_public.data.decode("utf-8")

    # Day title and overview
    assert "Jobra to Chika Campsite" in html
    assert "Day 1" in html

    # Event 1 details & variable facts
    assert ("Morning Briefing &amp; Gear Inspection" in html) or ("Morning Briefing & Gear Inspection" in html)
    assert "07:00 AM" in html
    assert "Trek team: 1 lead guide" in html
    assert "Pass on people: Always step aside towards the mountain wall" in html

    # Event 2 details & variable facts
    assert "River Crossing Checkpoint" in html
    assert "11:30 AM" in html
    assert "Safety rule: Form human chain" in html

    # Day Important Points section
    assert "Important Points for Day 1" in html
    assert "Altitude: 10,100 ft (initial acclimatization camp)" in html
    assert "Stay: 3-person dome alpine tents" in html
    assert "Network: No mobile network beyond Jobra roadhead" in html

    # Step 5: Download Itinerary PDF
    resp_pdf = client_a.get(f"/treks/{t_hash}/itinerary/pdf")
    assert resp_pdf.status_code == 200
    assert resp_pdf.headers["Content-Type"] == "application/pdf"
    assert resp_pdf.data.startswith(b"%PDF")

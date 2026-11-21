"""
Integration tests for Trek Itinerary Display Layouts (Option 2).
Verifies:
1. Default itinerary layout is 'accordion'.
2. Setting itinerary_layout to 'timeline' renders the mountain trail timeline component.
3. Setting itinerary_layout to 'magazine' renders the brochure magazine dossier component.
4. Admin can update the itinerary layout via POST /admin/treks/<id>/itinerary/layout.
"""

from datetime import date, timedelta
import pytest
from flask import g

from app.extensions import db
from app.models.trek import Trek
from app.models.trek_itinerary_day import TrekItineraryDay
from app.models.itinerary_element import ItineraryElement
from app.services import id_codec
from app.tenancy.scoping import tenant_context


def test_itinerary_layouts_rendering(app, org_a, make_user, client_a):
    """Verifies that each of the 3 itinerary layouts renders correctly on the public trek page."""
    with app.app_context(), tenant_context(org_a.id):
        admin = make_user(org_a, role="admin", name="Admin User", email="admin_layout@example.com")
        
        trek = Trek(
            organization_id=org_a.id,
            name="Har Ki Dun Valley",
            location="Uttarakhand",
            difficulty="moderate",
            duration_days=5,
            total_slots=10,
            available_slots=8,
            price=8500.0,
            status="open",
            itinerary_layout="accordion",
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=15),
            description="Ancient valley of gods with wooden temples and alpine meadows.",
        )
        db.session.add(trek)
        db.session.flush()

        day1 = TrekItineraryDay(
            organization_id=org_a.id,
            trek_id=trek.id,
            day_number=1,
            title="Sankri to Taluka Drive",
            description="Jeep drive along Supin river to roadhead.",
            important_points=["Keep waterproof jacket handy", "Check trekking pole grips"],
        )
        db.session.add(day1)
        db.session.flush()

        el1 = ItineraryElement(
            organization_id=org_a.id,
            itinerary_day_id=day1.id,
            sequence=1,
            element_type="transport",
            title="Jeep Transfer to Taluka",
            time_of_day="07:00 AM",
            description="Bumpy 12km dirt road drive through Govind National Park.",
            points=["4x4 Mahindra Bolero convoy", "Forest checkpost registration"],
        )
        db.session.add(el1)
        db.session.commit()

        trek_id = trek.id
        admin_id = admin.id

    t_hash = id_codec.encode(trek_id)

    # 1. Test Accordion Layout (Default)
    resp_acc = client_a.get(f"/treks/{t_hash}")
    assert resp_acc.status_code == 200
    html_acc = resp_acc.data.decode("utf-8")
    assert "itinerary-layout-accordion" in html_acc
    assert "Stage-by-Stage Itinerary" in html_acc
    assert "Jeep Transfer to Taluka" in html_acc
    assert "Forest checkpost registration" in html_acc

    # 2. Update layout to Timeline via Admin Endpoint
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
        sess["capability_keys"] = ["treks.edit_content"]

    resp_up_time = client_a.post(
        f"/admin/treks/{t_hash}/itinerary/layout",
        data={"itinerary_layout": "timeline"},
        follow_redirects=True,
    )
    assert resp_up_time.status_code == 200
    with app.app_context(), tenant_context(org_a.id):
        reloaded = db.session.get(Trek, trek_id)
        assert reloaded.itinerary_layout == "timeline"

    # Verify public view now renders Timeline
    resp_time = client_a.get(f"/treks/{t_hash}")
    assert resp_time.status_code == 200
    html_time = resp_time.data.decode("utf-8")
    assert "itinerary-layout-timeline" in html_time
    assert "Mountain Trail Timeline" in html_time
    assert "trail-timeline-container" in html_time
    assert "Jeep Transfer to Taluka" in html_time

    # 3. Update layout to Magazine Brochure via Admin Endpoint
    resp_up_mag = client_a.post(
        f"/admin/treks/{t_hash}/itinerary/layout",
        data={"itinerary_layout": "magazine"},
        follow_redirects=True,
    )
    assert resp_up_mag.status_code == 200
    with app.app_context(), tenant_context(org_a.id):
        reloaded = db.session.get(Trek, trek_id)
        assert reloaded.itinerary_layout == "magazine"

    # Verify public view now renders Magazine Brochure
    resp_mag = client_a.get(f"/treks/{t_hash}")
    assert resp_mag.status_code == 200
    html_mag = resp_mag.data.decode("utf-8")
    assert "itinerary-layout-magazine" in html_mag
    assert "Brochure Magazine Format" in html_mag
    assert "Expedition Field Dossier" in html_mag
    assert "EXPEDITION STAGE &bull; DAY 01" in html_mag or "EXPEDITION STAGE • DAY 01" in html_mag
    assert "Mandatory Stage Protocol" in html_mag
    assert "Jeep Transfer to Taluka" in html_mag

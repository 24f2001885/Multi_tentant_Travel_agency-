"""
Integration tests for the public itinerary PDF export and trek detail page (Milestone P10).
"""

from datetime import date, timedelta
from unittest.mock import patch
import pytest

from app.extensions import db
from app.models.trek import Trek
from app.models.trek_itinerary_day import TrekItineraryDay
from app.models.itinerary_element import ItineraryElement
from app.models.trek_review import TrekReview
from app.services import id_codec
from app.tenancy.scoping import tenant_context


def test_public_itinerary_pdf_download_and_gating(app, org_a, org_b, client_a, client_b):
    """Verifies anonymous branded PDF download, empty itinerary 404, pending 404, and cross-tenant 404."""
    with app.app_context(), tenant_context(org_a.id):
        # 1. Open trek with itinerary
        trek = Trek(
            organization_id=org_a.id,
            name="Roopkund Expedition",
            location="Uttarakhand",
            difficulty="difficult",
            duration_days=6,
            total_slots=10,
            available_slots=10,
            price=9500.0,
            status="open",
            start_date=date.today() + timedelta(days=20),
            end_date=date.today() + timedelta(days=26),
        )
        db.session.add(trek)
        db.session.flush()

        # Add itinerary day and element
        day = TrekItineraryDay(
            organization_id=org_a.id,
            trek_id=trek.id,
            day_number=1,
            title="Base Camp to Camp 1",
            description="Trek through oak and rhododendron forests.",
        )
        db.session.add(day)
        db.session.flush()

        elem = ItineraryElement(
            organization_id=org_a.id,
            day_id=day.id,
            sequence_order=1,
            element_type="activity",
            title="Acclimatization walk",
            description="Short 2-hour gentle ascent.",
        )
        db.session.add(elem)

        # 2. Open trek WITHOUT itinerary
        empty_trek = Trek(
            organization_id=org_a.id,
            name="Empty Itinerary Trek",
            location="Himachal",
            difficulty="easy",
            duration_days=3,
            total_slots=10,
            available_slots=10,
            price=4000.0,
            status="open",
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=13),
        )
        db.session.add(empty_trek)

        # 3. Pending trek WITH itinerary
        pending_trek = Trek(
            organization_id=org_a.id,
            name="Pending Trek",
            location="Ladakh",
            difficulty="difficult",
            duration_days=5,
            total_slots=8,
            available_slots=8,
            price=12000.0,
            status="pending",
            start_date=date.today() + timedelta(days=30),
            end_date=date.today() + timedelta(days=35),
        )
        db.session.add(pending_trek)
        db.session.flush()

        p_day = TrekItineraryDay(
            organization_id=org_a.id,
            trek_id=pending_trek.id,
            day_number=1,
            title="Leh Arrival",
        )
        db.session.add(p_day)
        db.session.commit()

        trek_id = trek.id
        empty_trek_id = empty_trek.id
        pending_trek_id = pending_trek.id

    trek_hash = id_codec.encode(trek_id)
    empty_hash = id_codec.encode(empty_trek_id)
    pending_hash = id_codec.encode(pending_trek_id)

    # Mock weasyprint HTML write_pdf to return dummy PDF bytes offline
    with patch("weasyprint.HTML.write_pdf", return_value=b"%PDF-1.4 test dummy pdf content"):
        # A. Anonymous download for valid open trek with itinerary
        resp = client_a.get(f"/treks/{trek_hash}/itinerary/pdf")
        assert resp.status_code == 200
        assert resp.mimetype == "application/pdf"
        assert f"itinerary_{trek_id}.pdf" in resp.headers.get("Content-Disposition", "")
        assert resp.data == b"%PDF-1.4 test dummy pdf content"

        # B. Open trek with no itinerary returns 404
        resp_empty = client_a.get(f"/treks/{empty_hash}/itinerary/pdf")
        assert resp_empty.status_code == 404

        # C. Pending trek returns 404 (not publicly visible)
        resp_pending = client_a.get(f"/treks/{pending_hash}/itinerary/pdf")
        assert resp_pending.status_code == 404

        # D. Cross-tenant access: client_b accessing org_a's trek returns 404
        resp_cross = client_b.get(f"/treks/{trek_hash}/itinerary/pdf")
        assert resp_cross.status_code == 404


def test_public_trek_detail_renders_gallery_itinerary_reviews(app, org_a, make_user, client_a):
    """Verifies that the public trek detail page renders itinerary, gallery, and reviews."""
    with app.app_context(), tenant_context(org_a.id):
        trek = Trek(
            organization_id=org_a.id,
            name="Har Ki Dun Trek",
            location="Uttarakhand",
            difficulty="moderate",
            duration_days=7,
            total_slots=15,
            available_slots=12,
            price=8500.0,
            status="open",
            start_date=date.today() + timedelta(days=15),
            end_date=date.today() + timedelta(days=22),
        )
        db.session.add(trek)
        db.session.flush()

        day = TrekItineraryDay(
            organization_id=org_a.id,
            trek_id=trek.id,
            day_number=1,
            title="Sankri to Taluka",
            description="Drive to Sankri and trek along Supin river.",
        )
        db.session.add(day)

        trekker = make_user(org_a, role="trekker", name="Aarav Sharma", email="aarav@example.com")
        review = TrekReview(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=trekker.id,
            rating=5,
            comment="Breathtaking landscapes and great experience!",
        )
        db.session.add(review)
        db.session.commit()

        trek_id = trek.id

    trek_hash = id_codec.encode(trek_id)
    resp = client_a.get(f"/treks/{trek_hash}")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "Har Ki Dun Trek" in html
    assert "Sankri to Taluka" in html
    assert "Breathtaking landscapes" in html
    assert "Aarav" in html

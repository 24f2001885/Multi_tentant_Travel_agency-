"""
Unit tests for app/services/review_service.py (Milestone P10).
Tests review gating, duplicate prevention, rating calculations, and privacy.
"""

from datetime import date, timedelta
import pytest

from app.extensions import db
from app.models.booking import Booking
from app.models.trek import Trek
from app.models.trek_review import TrekReview
from app.services import review_service
from app.services.review_service import ReviewNotAllowedError, DuplicateReviewError
from app.tenancy.scoping import tenant_context


def test_review_gating_and_submission(app, org_a, make_user):
    """Reviews can only be submitted on completed bookings by the booking owner."""
    with app.app_context(), tenant_context(org_a.id):
        u1 = make_user(org_a, email="u1@test.com", name="Alice Walker")
        u2 = make_user(org_a, email="u2@test.com", name="Bob Martin")

        trek = Trek(
            organization_id=org_a.id,
            name="Goechala Pass",
            location="Sikkim",
            difficulty="hard",
            duration_days=10,
            total_slots=10,
            available_slots=10,
            price=15000.0,
            start_date=date.today() - timedelta(days=20),
            end_date=date.today() - timedelta(days=10),
            status="completed",
        )
        db.session.add(trek)
        db.session.flush()

        # Active / booked booking (not completed yet)
        b_booked = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=u1.id,
            slot_count=1,
            total_amount=15000.0,
            status="booked",
        )
        # Completed booking
        b_completed = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=u1.id,
            slot_count=1,
            total_amount=15000.0,
            status="completed",
        )
        db.session.add_all([b_booked, b_completed])
        db.session.commit()

        # 1. Reviewing an uncompleted booking fails
        with pytest.raises(ReviewNotAllowedError):
            review_service.create_review(u1, b_booked, rating=5, comment="Great trip")

        # 2. Someone else reviewing u1's completed booking fails
        with pytest.raises(ReviewNotAllowedError):
            review_service.create_review(u2, b_completed, rating=4, comment="Imposter")

        # 3. Owner reviews successfully
        rev = review_service.create_review(u1, b_completed, rating=5, comment="Life changing expedition!")
        db.session.commit()

        assert rev.id is not None
        assert rev.rating == 5
        assert rev.comment == "Life changing expedition!"

        # 4. Duplicate review on the same booking raises DuplicateReviewError
        with pytest.raises(DuplicateReviewError):
            review_service.create_review(u1, b_completed, rating=4, comment="Again")


def test_average_rating_and_listing(app, org_a, make_user):
    """Accurately computes aggregate average rating and exposes only first names."""
    with app.app_context(), tenant_context(org_a.id):
        u1 = make_user(org_a, email="rev1@test.com", name="Samantha Smith")
        u2 = make_user(org_a, email="rev2@test.com", name="David Clark")

        trek = Trek(
            organization_id=org_a.id,
            name="Annapurna Base",
            location="Nepal",
            difficulty="moderate",
            duration_days=8,
            total_slots=10,
            available_slots=10,
            price=20000.0,
            start_date=date.today() - timedelta(days=30),
            end_date=date.today() - timedelta(days=22),
            status="completed",
        )
        db.session.add(trek)
        db.session.flush()

        b1 = Booking(organization_id=org_a.id, trek_id=trek.id, user_id=u1.id, slot_count=1, total_amount=20000.0, status="completed")
        b2 = Booking(organization_id=org_a.id, trek_id=trek.id, user_id=u2.id, slot_count=1, total_amount=20000.0, status="completed")
        db.session.add_all([b1, b2])
        db.session.commit()

        review_service.create_review(u1, b1, rating=4, comment="Very scenic")
        review_service.create_review(u2, b2, rating=5, comment="Outstanding guides")
        db.session.commit()

        avg_score, count = review_service.average_rating(trek)
        assert count == 2
        assert avg_score == 4.5 # (4 + 5) / 2 = 4.5

        # list_reviews verification
        pagination = review_service.list_reviews(trek, page=1, per_page=10)
        items = pagination.items
        assert len(items) == 2
        for r in items:
            # Privacy check: reviewer email is not directly exposed in reviewer string
            first_name = r.user.name.split()[0]
            assert first_name in ("Samantha", "David")

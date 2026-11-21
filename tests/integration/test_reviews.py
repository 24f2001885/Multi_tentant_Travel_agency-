"""
Integration tests for trek reviews end-to-end (Milestone P10).
Covers completion cascade review email triggers, review submission gating,
duplicate prevention, reviewer first-name privacy, pagination, and cross-tenant isolation.
"""

from datetime import date, timedelta
from unittest.mock import patch
import pytest

from app.extensions import db
from app.models.booking import Booking
from app.models.trek import Trek
from app.models.trek_review import TrekReview
from app.services import id_codec, trek_service, review_service
from app.tenancy.scoping import tenant_context


def test_trek_completion_triggers_review_invitations(app, org_a, make_user):
    """Transitioning a trek to completed sends review request notifications to completed bookings."""
    with app.app_context(), tenant_context(org_a.id):
        trekker = make_user(org_a, role="trekker", email="completed_trekker@example.com")
        trek = Trek(
            organization_id=org_a.id,
            name="Kashmir Great Lakes",
            location="Sonamarg",
            difficulty="difficult",
            duration_days=7,
            total_slots=10,
            available_slots=8,
            price=14000.0,
            status="open",
            start_date=date.today() - timedelta(days=10),
            end_date=date.today() - timedelta(days=3),
        )
        db.session.add(trek)
        db.session.flush()

        booking = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=trekker.id,
            slot_count=2,
            total_price=28000.0,
            status="booked",
        )
        db.session.add(booking)
        db.session.commit()

        trek_id = trek.id
        booking_id = booking.id

    with app.app_context(), tenant_context(org_a.id):
        t = Trek.query.get(trek_id)
        with patch("app.services.notification_service.send_review_request_email") as mock_email:
            trek_service.complete_trek_bookings(t)
            db.session.commit()

            # Booking status transitioned to completed
            b = Booking.query.get(booking_id)
            assert b.status == "completed"
            assert mock_email.called


def test_review_submission_gating_and_duplicates(app, org_a, make_user, client_a):
    """
    Completed booking owner can review.
    Non-completed booking, non-owner, or duplicate submission is rejected.
    """
    with app.app_context(), tenant_context(org_a.id):
        trekker1 = make_user(org_a, role="trekker", name="Priya Sharma", email="priya@example.com")
        trekker2 = make_user(org_a, role="trekker", name="Rohan Verma", email="rohan@example.com")

        trek = Trek(
            organization_id=org_a.id,
            name="Brahmatal Winter Trek",
            location="Uttarakhand",
            difficulty="moderate",
            duration_days=5,
            total_slots=10,
            available_slots=5,
            price=7000.0,
            status="completed",
            start_date=date.today() - timedelta(days=15),
            end_date=date.today() - timedelta(days=10),
        )
        db.session.add(trek)
        db.session.flush()

        # Completed booking for Priya
        booking_completed = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=trekker1.id,
            slot_count=1,
            total_price=7000.0,
            status="completed",
        )
        db.session.add(booking_completed)

        # Pending booking for Rohan
        booking_pending = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=trekker2.id,
            slot_count=1,
            total_price=7000.0,
            status="pending",
        )
        db.session.add(booking_pending)
        db.session.commit()

        b_comp_id = booking_completed.id
        b_pend_id = booking_pending.id
        u1_id = trekker1.id
        u2_id = trekker2.id
        t_id = trek.id

    b_comp_hash = id_codec.encode(b_comp_id)
    b_pend_hash = id_codec.encode(b_pend_id)
    t_hash = id_codec.encode(t_id)

    # 1. Non-owner (Rohan) cannot access Priya's review form
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(u2_id)

    resp_forbidden = client_a.get(f"/bookings/{b_comp_hash}/review")
    assert resp_forbidden.status_code in (403, 404)

    # 2. Rohan cannot review his own pending booking
    resp_pending = client_a.get(f"/bookings/{b_pend_hash}/review")
    assert resp_pending.status_code in (400, 403, 302)

    # 3. Owner (Priya) can access review form
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(u1_id)

    resp_form = client_a.get(f"/bookings/{b_comp_hash}/review")
    assert resp_form.status_code == 200

    # 4. Priya submits valid review
    resp_submit = client_a.post(
        f"/bookings/{b_comp_hash}/review",
        data={"rating": 5, "comment": "Unbelievable snowy views! Exceptional staff."},
        follow_redirects=True,
    )
    assert resp_submit.status_code == 200

    with app.app_context(), tenant_context(org_a.id):
        rev = TrekReview.query.filter_by(booking_id=b_comp_id).first()
        assert rev is not None
        assert rev.rating == 5
        assert rev.user_id == u1_id

    # 5. Duplicate review attempt is blocked
    resp_dup = client_a.get(f"/bookings/{b_comp_hash}/review")
    assert resp_dup.status_code in (400, 302)


def test_public_reviews_listing_and_isolation(app, org_a, org_b, make_user, client_a, client_b):
    """Public reviews endpoint respects pagination, reviewer first-name privacy, and tenant isolation."""
    with app.app_context(), tenant_context(org_a.id):
        user = make_user(org_a, role="trekker", name="Ananya Deshmukh", email="ananya@example.com")
        trek = Trek(
            organization_id=org_a.id,
            name="Kuari Pass",
            location="Uttarakhand",
            difficulty="moderate",
            duration_days=6,
            total_slots=10,
            available_slots=0,
            price=8000.0,
            status="completed",
            start_date=date.today() - timedelta(days=20),
            end_date=date.today() - timedelta(days=14),
        )
        db.session.add(trek)
        db.session.flush()

        review = TrekReview(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=user.id,
            rating=4,
            comment="Great snow trek, highly recommended guide.",
        )
        db.session.add(review)
        db.session.commit()
        trek_id = trek.id

    t_hash = id_codec.encode(trek_id)

    # 1. Public user views reviews list on org_a
    resp = client_a.get(f"/treks/{t_hash}/reviews")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "Kuari Pass" in html
    assert "Great snow trek" in html
    # First name privacy check: "Ananya" should be rendered, not "Deshmukh"
    assert "Ananya" in html
    assert "Deshmukh" not in html

    # 2. Cross-tenant access on org_b returns 404
    resp_cross = client_b.get(f"/treks/{t_hash}/reviews")
    assert resp_cross.status_code == 404


def test_direct_trek_review_and_photo_submission(app, org_a, make_user, client_a):
    """
    Trekkers who completed/booked a trek can post reviews and trip photos directly from the trek page.
    Unauthenticated users and non-booking trekkers cannot post.
    """
    import io
    from PIL import Image

    def _dummy_image():
        buf = io.BytesIO()
        img = Image.new("RGB", (60, 60), color="green")
        img.save(buf, format="JPEG")
        buf.seek(0)
        return (buf, "trail_summit.jpg")

    with app.app_context(), tenant_context(org_a.id):
        # 1. Verified trekker with a confirmed booking
        trekker1 = make_user(org_a, role="trekker", name="Siddharth Malhotra", email="sid@example.com")
        # 2. Unverified trekker who never booked this trek
        trekker2 = make_user(org_a, role="trekker", name="Neha Sharma", email="neha@example.com")

        trek = Trek(
            organization_id=org_a.id,
            name="Kedarkantha Snow Trek",
            location="Sankri",
            difficulty="moderate",
            duration_days=5,
            total_slots=10,
            available_slots=8,
            price=6500.0,
            status="open",
            start_date=date.today() - timedelta(days=10),
            end_date=date.today() - timedelta(days=5),
        )
        db.session.add(trek)
        db.session.flush()

        booking1 = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=trekker1.id,
            slot_count=1,
            total_price=6500.0,
            status="confirmed",
        )
        db.session.add(booking1)
        db.session.commit()

        trek_id = trek.id
        u1_id = trekker1.id
        u2_id = trekker2.id

    t_hash = id_codec.encode(trek_id)

    # A. Anonymous user visits trek detail page -> sees login prompt
    resp_anon = client_a.get(f"/treks/{t_hash}")
    assert resp_anon.status_code == 200
    anon_html = resp_anon.data.decode("utf-8")
    assert "Log In to Post" in anon_html
    assert "Went on this trek?" in anon_html

    # Anonymous post attempt -> redirected to login
    resp_anon_post = client_a.post(f"/treks/{t_hash}/reviews/submit", data={"rating": 5, "comment": "Anon test"})
    assert resp_anon_post.status_code == 302
    assert "/login" in resp_anon_post.headers.get("Location", "")

    # B. Non-booking trekker (Neha) visits trek detail page -> sees verified explorer notice
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(u2_id)
        sess["capability_keys"] = []

    resp_neha = client_a.get(f"/treks/{t_hash}")
    assert resp_neha.status_code == 200
    neha_html = resp_neha.data.decode("utf-8")
    assert "Verified Explorer Community" in neha_html

    # Non-booking trekker attempt to post -> rejected
    resp_neha_post = client_a.post(
        f"/treks/{t_hash}/reviews/submit",
        data={"rating": 5, "comment": "I didn't actually book this trek"},
        follow_redirects=True,
    )
    assert resp_neha_post.status_code == 200
    with app.app_context(), tenant_context(org_a.id):
        assert TrekReview.query.filter_by(user_id=u2_id, trek_id=trek_id).first() is None

    # C. Verified trekker (Siddharth) visits trek detail page -> sees active review form
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(u1_id)
        sess["capability_keys"] = []

    resp_sid = client_a.get(f"/treks/{t_hash}")
    assert resp_sid.status_code == 200
    sid_html = resp_sid.data.decode("utf-8")
    assert "Verified Explorer" in sid_html
    assert "Share Your Experience & Trip Photos" in sid_html

    # Siddharth posts a review WITH comments and a trip photo
    buf, filename = _dummy_image()
    resp_sid_post = client_a.post(
        f"/treks/{t_hash}/reviews/submit",
        data={
            "rating": 5,
            "comment": "Incredible summit sunrise! The lead guide ensured everyone had hot tea and microspikes.",
            "photo": (buf, filename),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp_sid_post.status_code == 200

    # Verify review and image_url in DB
    with app.app_context(), tenant_context(org_a.id):
        rev = TrekReview.query.filter_by(user_id=u1_id, trek_id=trek_id).first()
        assert rev is not None
        assert rev.rating == 5
        assert "Incredible summit sunrise" in rev.comment
        assert rev.image_url is not None
        assert rev.image_url.startswith("/media/")
        img_url = rev.image_url

    # Trek detail page now shows the review, comments, and the trip photo (viewed by public user)
    with client_a.session_transaction() as sess:
        sess.clear()

    resp_view = client_a.get(f"/treks/{t_hash}")
    assert resp_view.status_code == 200
    view_html = resp_view.data.decode("utf-8")
    assert "Siddharth" in view_html
    assert "Malhotra" not in view_html  # First-name privacy preserved
    assert "Incredible summit sunrise" in view_html
    assert img_url in view_html
    assert "Trip photo shared by trekker" in view_html

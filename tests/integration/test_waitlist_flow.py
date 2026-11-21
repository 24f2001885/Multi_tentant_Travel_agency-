"""
Integration tests for waitlist join -> offer -> convert / expire (Milestone P9).
Tests full waitlist lifecycle with live capacity checks and converted status.
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
import pytest

from app.extensions import db
from app.models.booking import Booking
from app.models.trek import Trek
from app.models.waitlist_entry import WaitlistEntry
from app.services import payment_service, waitlist_service
from app.services.id_codec import encode
from app.tenancy.scoping import tenant_context


def test_waitlist_full_lifecycle(app, org_a, make_user, client_a):
    """Waitlist join -> cancellation promotes earliest -> booking converts entry."""
    with app.app_context(), tenant_context(org_a.id):
        u1 = make_user(org_a, role="trekker", email="holder@example.com")
        u2 = make_user(org_a, role="trekker", email="waitlister@example.com")

        trek = Trek(
            organization_id=org_a.id,
            title="Har Ki Dun",
            start_date=date.today() + timedelta(days=20),
            end_date=date.today() + timedelta(days=25),
            total_slots=1,
            available_slots=0, # Completely full
            price_per_slot=1500.0,
            status="open",
        )
        db.session.add(trek)
        db.session.flush()

        # u1 holds the 1 slot
        b1 = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=u1.id,
            slot_count=1,
            total_amount=1500.0,
            status="booked",
        )
        db.session.add(b1)
        db.session.commit()

        # u2 joins waitlist via route
        with client_a.session_transaction() as sess:
            sess["_user_id"] = str(u2.id)

        trek_hash = encode(trek.id)
        resp_join = client_a.post(
            f"/treks/{trek_hash}/waitlist",
            data={"requested_slot_count": 1},
            follow_redirects=True,
        )
        assert resp_join.status_code == 200

        entry = WaitlistEntry.query.filter_by(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=u2.id,
        ).first()
        assert entry is not None
        assert entry.status == "waiting"

        # u1 cancels -> triggers offer for u2
        with patch("app.services.notification_service.send_waitlist_offer_email") as mock_email:
            payment_service.cancel_booking(b1, u1, "customer_requested")
            db.session.commit()
            assert mock_email.called

        db.session.refresh(entry)
        assert entry.status == "offered"
        assert entry.offer_expires_at is not None

        # During live offer, a third user (u3) cannot book
        u3 = make_user(org_a, role="trekker", email="outsider@example.com")
        avail_u3 = waitlist_service.effective_available(trek, for_user_id=u3.id)
        assert avail_u3 == 0

        # But u2 (the offer holder) can see the slot available
        avail_u2 = waitlist_service.effective_available(trek, for_user_id=u2.id)
        assert avail_u2 == 1

        # u2 completes booking -> waitlist entry marked converted
        waitlist_service.mark_converted(entry)
        db.session.commit()
        db.session.refresh(entry)
        assert entry.status == "converted"

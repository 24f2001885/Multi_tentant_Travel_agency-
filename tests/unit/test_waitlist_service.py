"""
Unit tests for app/services/waitlist_service.py (Milestone P9).
Tests waitlist enrollment, FIFO promotion, effective availability, and offer expiry.
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
import pytest

from app.extensions import db
from app.models.trek import Trek
from app.models.waitlist_entry import WaitlistEntry
from app.services import waitlist_service
from app.services.waitlist_service import AlreadyWaitlistedError, WaitlistError
from app.tenancy.scoping import tenant_context


def test_join_waitlist_flow(app, org_a, make_user):
    """Enrollment succeeds when requested slots exceed available capacity."""
    with app.app_context(), tenant_context(org_a.id):
        u1 = make_user(org_a, email="u1@test.com")
        trek = Trek(
            organization_id=org_a.id,
            title="Kedarkantha Winter",
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=15),
            total_slots=10,
            available_slots=0, # Full trek
            price_per_slot=1500.0,
            status="open",
        )
        db.session.add(trek)
        db.session.commit()

        entry = waitlist_service.join_waitlist(u1, trek, requested_slot_count=2)
        assert entry.id is not None
        assert entry.status == "waiting"
        assert entry.requested_slot_count == 2

        # Duplicate join raises AlreadyWaitlistedError
        with pytest.raises(AlreadyWaitlistedError):
            waitlist_service.join_waitlist(u1, trek, requested_slot_count=1)


def test_join_waitlist_rejected_when_slots_available(app, org_a, make_user):
    """Cannot join waitlist when slots are directly available to book."""
    with app.app_context(), tenant_context(org_a.id):
        u1 = make_user(org_a)
        trek = Trek(
            organization_id=org_a.id,
            title="Hampta Pass",
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=15),
            total_slots=10,
            available_slots=3,
            price_per_slot=1500.0,
            status="open",
        )
        db.session.add(trek)
        db.session.commit()

        with pytest.raises(WaitlistError):
            waitlist_service.join_waitlist(u1, trek, requested_slot_count=2)


def test_effective_available_with_offers(app, org_a, make_user):
    """Live offers hold effective availability against other users, but not for the offer holder."""
    with app.app_context(), tenant_context(org_a.id):
        u1 = make_user(org_a, email="user1@test.com")
        u2 = make_user(org_a, email="user2@test.com")
        trek = Trek(
            organization_id=org_a.id,
            title="Kashmir Great Lakes",
            start_date=date.today() + timedelta(days=20),
            end_date=date.today() + timedelta(days=25),
            total_slots=5,
            available_slots=2,
            price_per_slot=2500.0,
            status="open",
        )
        db.session.add(trek)
        db.session.commit()

        # Place a live offer for u1 for 2 slots
        now = datetime.now(timezone.utc)
        entry = WaitlistEntry(
            organization_id=org_a.id,
            user_id=u1.id,
            trek_id=trek.id,
            requested_slot_count=2,
            status="offered",
            offered_at=now,
            offer_expires_at=now + timedelta(hours=2),
        )
        db.session.add(entry)
        db.session.commit()

        # For u2, effective available should be 0 (2 - 2 = 0)
        assert waitlist_service.effective_available(trek, for_user_id=u2.id) == 0

        # For u1 (the offer holder), effective available should be 2
        assert waitlist_service.effective_available(trek, for_user_id=u1.id) == 2


def test_on_slots_freed_fifo_promotion(app, org_a, make_user):
    """FIFO promotion promotes earliest waiting entry when capacity accommodates them."""
    with app.app_context(), tenant_context(org_a.id):
        u1 = make_user(org_a, email="first@test.com")
        u2 = make_user(org_a, email="second@test.com")
        trek = Trek(
            organization_id=org_a.id,
            title="Roopkund",
            start_date=date.today() + timedelta(days=30),
            end_date=date.today() + timedelta(days=35),
            total_slots=10,
            available_slots=0,
            price_per_slot=3000.0,
            status="open",
        )
        db.session.add(trek)
        db.session.commit()

        e1 = waitlist_service.join_waitlist(u1, trek, requested_slot_count=1)
        e2 = waitlist_service.join_waitlist(u2, trek, requested_slot_count=1)

        # Free 1 slot
        trek.available_slots = 1
        db.session.commit()

        with patch("app.services.notification_service.send_waitlist_offer_email") as mock_email:
            promoted = waitlist_service.on_slots_freed(trek)
            assert len(promoted) == 1
            assert promoted[0].id == e1.id
            assert promoted[0].status == "offered"
            assert mock_email.called

            # Check that e2 is still waiting
            db.session.refresh(e2)
            assert e2.status == "waiting"


def test_expire_offers_and_reoffer(app, org_a, make_user):
    """Overdue offers are expired by the worker, freeing capacity to promote the next candidate."""
    with app.app_context(), tenant_context(org_a.id):
        u1 = make_user(org_a, email="exp1@test.com")
        u2 = make_user(org_a, email="wait2@test.com")
        trek = Trek(
            organization_id=org_a.id,
            title="Valley of Flowers",
            start_date=date.today() + timedelta(days=15),
            end_date=date.today() + timedelta(days=20),
            total_slots=5,
            available_slots=1,
            price_per_slot=2000.0,
            status="open",
        )
        db.session.add(trek)
        db.session.commit()

        past = datetime.now(timezone.utc) - timedelta(hours=3)
        e1 = WaitlistEntry(
            organization_id=org_a.id,
            user_id=u1.id,
            trek_id=trek.id,
            requested_slot_count=1,
            status="offered",
            offered_at=past - timedelta(hours=2),
            offer_expires_at=past, # Overdue
        )
        db.session.add(e1)

        e2 = WaitlistEntry(
            organization_id=org_a.id,
            user_id=u2.id,
            trek_id=trek.id,
            requested_slot_count=1,
            status="waiting",
        )
        db.session.add(e2)
        db.session.commit()

        with patch("app.services.notification_service.send_waitlist_offer_email"):
            expired_count = waitlist_service.expire_offers()
            assert expired_count == 1

            db.session.refresh(e1)
            assert e1.status == "expired"

            db.session.refresh(e2)
            assert e2.status == "offered"

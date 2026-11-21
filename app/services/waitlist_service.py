"""
Waitlist join, offer, expiry, and promotion logic (TDR v2 §3.5, §11).
Framework-agnostic.
"""

from datetime import datetime, timedelta, timezone
import os
from typing import List, Optional

from app.extensions import db
from app.models.waitlist_entry import WaitlistEntry
from app.models.trek import Trek
from app.tenancy.scoping import tenant_bypass
from app.services import notification_service


class AlreadyWaitlistedError(Exception):
    """Raised when a user attempts to join a waitlist they already hold an active position on."""
    pass


class WaitlistError(Exception):
    """Generic waitlist validation error."""
    pass


def effective_available(trek: Trek, for_user_id: Optional[int] = None) -> int:
    """
    Computes effective available slots on a trek.
    Excludes slots reserved by live 'offered' waitlist entries held by *other* users.
    """
    now = datetime.now(timezone.utc)
    # Query live offers on this trek
    offered_query = WaitlistEntry.query.filter(
        WaitlistEntry.trek_id == trek.id,
        WaitlistEntry.status == "offered",
        WaitlistEntry.offer_expires_at > now,
    )
    if for_user_id is not None:
        offered_query = offered_query.filter(WaitlistEntry.user_id != for_user_id)

    held_slots = sum(e.requested_slot_count for e in offered_query.all())
    return max(0, (trek.available_slots or 0) - held_slots)


def join_waitlist(user, trek: Trek, requested_slot_count: int = 1) -> WaitlistEntry:
    """
    Enrolls a user onto the FIFO waitlist for an open trek.
    """
    if trek.status != "open":
        raise WaitlistError("Cannot join waitlist for a trek that is not open.")

    if requested_slot_count < 1:
        raise WaitlistError("Requested slot count must be at least 1.")

    avail = effective_available(trek, for_user_id=user.id)
    if requested_slot_count <= avail:
        raise WaitlistError("Slots are currently available; proceed directly to booking.")

    # Check for active waitlist entry
    existing = WaitlistEntry.query.filter(
        WaitlistEntry.user_id == user.id,
        WaitlistEntry.trek_id == trek.id,
        WaitlistEntry.status.in_(("waiting", "offered")),
    ).first()
    if existing:
        raise AlreadyWaitlistedError("You are already on the waitlist for this trek.")

    entry = WaitlistEntry(
        organization_id=trek.organization_id,
        user_id=user.id,
        trek_id=trek.id,
        requested_slot_count=requested_slot_count,
        status="waiting",
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def leave_waitlist(entry: WaitlistEntry, user) -> None:
    """
    Allows a trekker to cancel their own waitlist entry.
    """
    if entry.user_id != user.id:
        raise PermissionError("Cannot modify another user's waitlist entry.")

    if entry.status in ("waiting", "offered"):
        entry.status = "expired"
        db.session.commit()


def on_slots_freed(trek: Trek) -> List[WaitlistEntry]:
    """
    Promotes waiting entries in FIFO order if newly freed capacity accommodates them.
    Fires offer emails.
    """
    promoted: List[WaitlistEntry] = []
    window_minutes = int(os.environ.get("WAITLIST_OFFER_WINDOW_MINUTES", 120))

    # Query waiting entries ordered FIFO
    waiting_entries = WaitlistEntry.query.filter(
        WaitlistEntry.trek_id == trek.id,
        WaitlistEntry.status == "waiting",
    ).order_by(WaitlistEntry.created_at.asc()).all()

    for entry in waiting_entries:
        curr_avail = effective_available(trek)
        if entry.requested_slot_count <= curr_avail:
            now = datetime.now(timezone.utc)
            entry.status = "offered"
            entry.offered_at = now
            entry.offer_expires_at = now + timedelta(minutes=window_minutes)
            promoted.append(entry)
            db.session.flush()

            try:
                notification_service.send_waitlist_offer_email(entry)
            except Exception:
                pass

    if promoted:
        db.session.commit()

    return promoted


def expire_offers() -> int:
    """
    Periodic worker to expire overdue offers and promote the next waiting trekkers.
    Runs under tenant_bypass.
    """
    now = datetime.now(timezone.utc)
    expired_count = 0
    affected_treks: set[int] = set()

    with tenant_bypass():
        overdue_offers = WaitlistEntry.query.filter(
            WaitlistEntry.status == "offered",
            WaitlistEntry.offer_expires_at <= now,
        ).all()

        for offer in overdue_offers:
            offer.status = "expired"
            affected_treks.add(offer.trek_id)
            expired_count += 1

        db.session.commit()

        for tid in affected_treks:
            t = Trek.query.get(tid)
            if t and t.status == "open":
                on_slots_freed(t)

    return expired_count


def mark_converted(entry: WaitlistEntry) -> None:
    """
    Marks an offered waitlist entry as converted upon confirmed booking.
    """
    entry.status = "converted"
    db.session.flush()

"""
Integration tests for booking slot concurrency and race condition resilience (TDR v2 §3.1, Milestone P7).
Verifies PostgreSQL SELECT ... FOR UPDATE row-level locking behavior.
"""

from pathlib import Path
import threading
import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Booking, Payment, Trek
from app.services import payment_service
from app.tenancy.scoping import tenant_context


def test_guard_no_begin_immediate_in_codebase():
    """Guard test verifying SQLite's BEGIN IMMEDIATE pattern has been completely eliminated from app/."""
    app_dir = Path(__file__).resolve().parent.parent.parent / "app"
    matches = []

    for file_path in app_dir.rglob("*.py"):
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        if "BEGIN IMMEDIATE" in content:
            matches.append(str(file_path))

    assert len(matches) == 0, f"Found legacy 'BEGIN IMMEDIATE' in: {matches}"


@pytest.mark.postgres_only
def test_concurrent_last_slot_booking_confirmation_race(app, org_a, make_user, make_trek):
    """
    Two concurrent payment confirmations for the last remaining slot on a trek.
    Row-level locking guarantees exactly one succeeds ('booked') while the other
    is cancelled and refunded ('cancelled' / 'refunded').
    """
    with app.app_context(), tenant_context(org_a.id):
        # 1. Setup trek with exactly 1 available slot
        trek = make_trek(org_a, name="Kuari Pass", total_slots=1, available_slots=1, status="open")
        user_1 = make_user(org_a, email="racer1@example.com")
        user_2 = make_user(org_a, email="racer2@example.com")

        # 2. Initiate two bookings and mock gateway orders
        booking_1 = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=user_1.id,
            status="pending",
            payment_status="pending",
        )
        booking_2 = Booking(
            organization_id=org_a.id,
            trek_id=trek.id,
            user_id=user_2.id,
            status="pending",
            payment_status="pending",
        )
        db.session.add_all([booking_1, booking_2])
        db.session.flush()

        order_1 = payment_service.create_gateway_order(booking_1)
        order_2 = payment_service.create_gateway_order(booking_2)
        db.session.commit()

        order_id_1 = order_1["id"]
        order_id_2 = order_2["id"]
        trek_id = trek.id
        booking_id_1 = booking_1.id
        booking_id_2 = booking_2.id
        org_id = org_a.id

    barrier = threading.Barrier(2)
    exceptions = []

    def confirm_worker(order_id, tx_id):
        try:
            with app.app_context(), tenant_context(org_id):
                barrier.wait(timeout=10)
                payment_service.confirm_payment(
                    gateway_order_id=order_id,
                    gateway_status="success",
                    transaction_id=tx_id,
                )
        except Exception as exc:
            exceptions.append(exc)

    t1 = threading.Thread(target=confirm_worker, args=(order_id_1, "pay_tx_111"))
    t2 = threading.Thread(target=confirm_worker, args=(order_id_2, "pay_tx_222"))

    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert len(exceptions) == 0, f"Thread encountered exception: {exceptions}"

    with app.app_context(), tenant_context(org_a.id):
        refreshed_trek = db.session.get(Trek, trek_id)
        b1 = db.session.get(Booking, booking_id_1)
        b2 = db.session.get(Booking, booking_id_2)

        # print(f"[DEBUG] pg race final_slots={refreshed_trek.available_slots}")
        assert refreshed_trek.available_slots == 0

        statuses = {b1.status, b2.status}
        payment_statuses = {b1.payment_status, b2.payment_status}

        assert statuses == {"booked", "cancelled"}
        assert payment_statuses == {"paid", "refunded"}

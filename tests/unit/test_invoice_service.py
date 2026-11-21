"""
Unit tests for app/services/invoice_service.py (Milestone P9).
Tests sequential invoice numbering, GST math, idempotency, and PDF retrieval.
"""

from decimal import Decimal
import os
import pytest

from app.extensions import db
from app.models.booking import Booking
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.trek import Trek
from app.services import invoice_service
from app.tenancy.scoping import tenant_context


def test_generate_invoice_skipped_without_prerequisites(app, org_a, make_user):
    """Invoice generation is skipped with warning when GST number or GST rate is missing."""
    with app.app_context(), tenant_context(org_a.id):
        u = make_user(org_a)
        booking = Booking(
            organization_id=org_a.id,
            user_id=u.id,
            slot_count=1,
            total_amount=1000.0,
            status="booked",
        )
        db.session.add(booking)
        db.session.commit()

        # Both unset
        inv = invoice_service.generate_invoice(booking)
        assert inv is None


def test_generate_invoice_sequential_and_tax_math(app, org_a, make_user, monkeypatch):
    """Generates sequential invoices and computes tax amounts accurately under GST-inclusive model."""
    monkeypatch.setenv("GST_RATE_PERCENT", "5.0") # 5% GST

    with app.app_context(), tenant_context(org_a.id):
        org_a.gst_number = "27AAAAA0000A1Z5"
        db.session.commit()

        u = make_user(org_a)
        b1 = Booking(
            organization_id=org_a.id,
            user_id=u.id,
            slot_count=1,
            total_amount=1050.0,
            status="booked",
        )
        db.session.add(b1)
        db.session.flush()

        p1 = Payment(
            organization_id=org_a.id,
            booking_id=b1.id,
            amount=1050.0,
            currency="INR",
            status="paid",
        )
        db.session.add(p1)
        db.session.commit()

        inv1 = invoice_service.generate_invoice(b1)
        assert inv1 is not None
        assert inv1.invoice_number == "INV-00001"
        assert inv1.total_amount == Decimal("1050.00")
        assert inv1.taxable_amount == Decimal("1000.00")
        assert inv1.gst_amount == Decimal("50.00")

        # Second booking -> INV-00002
        b2 = Booking(
            organization_id=org_a.id,
            user_id=u.id,
            slot_count=1,
            total_amount=2100.0,
            status="booked",
        )
        db.session.add(b2)
        db.session.flush()

        p2 = Payment(
            organization_id=org_a.id,
            booking_id=b2.id,
            amount=2100.0,
            currency="INR",
            status="paid",
        )
        db.session.add(p2)
        db.session.commit()

        inv2 = invoice_service.generate_invoice(b2)
        assert inv2 is not None
        assert inv2.invoice_number == "INV-00002"

        # Idempotency check: generating for b1 again returns inv1 without incrementing sequence
        inv1_again = invoice_service.generate_invoice(b1)
        assert inv1_again.id == inv1.id
        assert inv1_again.invoice_number == "INV-00001"


def test_get_invoice_pdf_bytes(app, org_a, make_user, monkeypatch):
    """Retrieves PDF bytes written to disk storage."""
    monkeypatch.setenv("GST_RATE_PERCENT", "18.0")

    with app.app_context(), tenant_context(org_a.id):
        org_a.gst_number = "29ABCDE1234F1Z5"
        db.session.commit()

        u = make_user(org_a)
        b = Booking(
            organization_id=org_a.id,
            user_id=u.id,
            slot_count=1,
            total_amount=1180.0,
            status="booked",
        )
        db.session.add(b)
        db.session.flush()

        p = Payment(
            organization_id=org_a.id,
            booking_id=b.id,
            amount=1180.0,
            currency="INR",
            status="paid",
        )
        db.session.add(p)
        db.session.commit()

        inv = invoice_service.generate_invoice(b)
        assert inv is not None

        pdf_data = invoice_service.get_invoice_pdf_bytes(inv)
        assert pdf_data is not None
        assert pdf_data.startswith(b"%PDF")

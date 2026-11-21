"""
The invoices table — GST invoice records (TDR v2 §4.4).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
import uuid
import sqlalchemy as sa
from sqlalchemy import UniqueConstraint

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class Invoice(TenantScopedMixin, db.Model):
    __tablename__ = "invoices"

    id = db.Column(sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), nullable=False, unique=True, index=True)
    invoice_number = db.Column(db.String(50), nullable=False)
    taxable_amount = db.Column(db.Numeric(10, 2), nullable=False)
    gst_amount = db.Column(db.Numeric(10, 2), nullable=False)
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    pdf_asset_url = db.Column(db.String(500), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "invoice_number", name="uq_invoice_org_number"),
        UniqueConstraint("booking_id", name="uq_invoice_booking"),
    )

    booking = db.relationship("Booking", backref=db.backref("invoice", uselist=False))

    def __repr__(self) -> str:
        # print(f"[DEBUG] invoice {self.invoice_number} total={self.total_amount}")
        return f"<Invoice {self.id} number='{self.invoice_number}' total={self.total_amount}>"

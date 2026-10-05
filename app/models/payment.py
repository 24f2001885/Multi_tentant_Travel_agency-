"""
The payments table — records payment attempts, gateways, and statuses (TDR v2 §4.3).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
import sqlalchemy as sa
from sqlalchemy import CheckConstraint

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class Payment(TenantScopedMixin, db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), nullable=False, index=True)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="INR")
    gateway_order_id = db.Column(db.String(100), nullable=False, index=True)
    transaction_id = db.Column(db.String(100), unique=True, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="initiated")
    payment_date = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('initiated', 'success', 'failed', 'refunded')",
            name="check_valid_payment_status",
        ),
    )

    booking = db.relationship("Booking", back_populates="payments", lazy=True)

    def __repr__(self) -> str:
        # print(f"[DEBUG] {self.__class__.__name__} id={self.id} org={self.organization_id}")
        return f"<Payment {self.id} booking_id={self.booking_id} status={self.status} org={self.organization_id}>"

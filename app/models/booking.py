"""
The bookings table — records a trekker's reservation on a trek (TDR v2 §4.3).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
import sqlalchemy as sa
from sqlalchemy import text

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class Booking(TenantScopedMixin, db.Model):
    __tablename__ = "bookings"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    trek_id = db.Column(db.Integer, db.ForeignKey("treks.id"), nullable=True, index=True)
    booking_date = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    status = db.Column(db.String(20), nullable=False, default="pending")
    payment_status = db.Column(db.String(20), nullable=False, default="pending")
    slot_count = db.Column(db.Integer, nullable=False, default=1)
    total_price = db.Column(db.Numeric(10, 2), nullable=True)
    booking_channel = db.Column(db.String(10), nullable=False, default="online")
    manual_sign_off_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    manual_sign_off_at = db.Column(db.DateTime(timezone=True), nullable=True)
    cancellation_reason = db.Column(db.String(30), nullable=True)
    cancelled_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    refund_percentage_applied = db.Column(db.Numeric(5, 2), nullable=True)
    coupon_id = db.Column(sa.UUID(as_uuid=True), db.ForeignKey("coupons.id"), nullable=True)

    @property
    def total_amount(self):
        return self.total_price

    @total_amount.setter
    def total_amount(self, value):
        self.total_price = value

    @property
    def slots(self):
        return self.slot_count

    @slots.setter
    def slots(self, value):
        self.slot_count = value

    @property
    def created_at(self):
        return self.booking_date

    @created_at.setter
    def created_at(self, value):
        self.booking_date = value

    @property
    def refund_percentage(self):
        return self.refund_percentage_applied

    @refund_percentage.setter
    def refund_percentage(self, value):
        self.refund_percentage_applied = value

    @property
    def refund_amount(self):
        if getattr(self, "_refund_amount", None) is not None:
            return self._refund_amount
        if self.refund_percentage_applied is None:
            return None
        from decimal import Decimal
        total = self.total_price or self.total_amount or Decimal("0.00")
        return (Decimal(str(total)) * (Decimal(str(self.refund_percentage_applied)) / Decimal("100.00"))).quantize(Decimal("0.01"))

    @refund_amount.setter
    def refund_amount(self, value):
        self._refund_amount = value

    __table_args__ = (
        db.CheckConstraint(
            "status IN ('pending', 'booked', 'confirmed', 'cancelled', 'completed')",
            name="check_valid_booking_status",
        ),
        db.CheckConstraint(
            "payment_status IN ('pending', 'paid', 'failed', 'refunded')",
            name="check_valid_payment_status",
        ),
        db.CheckConstraint("slot_count > 0", name="check_valid_slot_count"),
        db.CheckConstraint(
            "booking_channel IN ('online', 'manual')",
            name="check_valid_booking_channel",
        ),
        db.CheckConstraint(
            "cancellation_reason IS NULL OR cancellation_reason IN ('customer_requested', 'staff_force_cancel', 'payment_failed', 'identity_verification_failed')",
            name="check_valid_cancellation_reason",
        ),
        # DB-level duplicate-booking guard (partial unique index supporting both SQLite and Postgres)
        db.Index(
            "ix_unique_active_booking",
            "user_id",
            "trek_id",
            unique=True,
            sqlite_where=text("status IN ('pending','booked','confirmed')"),
            postgresql_where=text("status IN ('pending','booked','confirmed')"),
        ),
    )

    # Relationships
    organization = db.relationship("Organization", foreign_keys="[Booking.organization_id]", lazy=True)
    user = db.relationship("User", foreign_keys=[user_id], back_populates="bookings", lazy=True)
    trek = db.relationship("Trek", back_populates="bookings", lazy=True)
    payments = db.relationship(
        "Payment",
        back_populates="booking",
        lazy=True,
        cascade="all, delete-orphan",
    )
    participants = db.relationship(
        "BookingParticipant",
        back_populates="booking",
        cascade="all, delete-orphan",
        order_by="BookingParticipant.is_primary_contact.desc()",
        lazy=True,
    )
    signer = db.relationship("User", foreign_keys=[manual_sign_off_by], lazy=True)
    canceller = db.relationship("User", foreign_keys=[cancelled_by], lazy=True)
    coupon = db.relationship("Coupon", foreign_keys=[coupon_id], lazy=True)

    @property
    def payment(self):
        return self.payments[-1] if self.payments else None

    def __repr__(self) -> str:
        # print(f"[DEBUG] booking {self.id} slots={self.slot_count} channel={self.booking_channel} reason={self.cancellation_reason}")
        # print(f"[DEBUG] booking {self.id} org={self.organization_id} status={self.status}")
        return f"<Booking {self.id} user_id={self.user_id} trek_id={self.trek_id} slots={self.slot_count} status={self.status}>"


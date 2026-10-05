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
    trek_id = db.Column(db.Integer, db.ForeignKey("treks.id"), nullable=False, index=True)
    booking_date = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    status = db.Column(db.String(20), nullable=False, default="pending")
    payment_status = db.Column(db.String(20), nullable=False, default="pending")

    __table_args__ = (
        db.CheckConstraint(
            "status IN ('pending', 'booked', 'cancelled', 'completed')",
            name="check_valid_booking_status",
        ),
        db.CheckConstraint(
            "payment_status IN ('pending', 'paid', 'failed', 'refunded')",
            name="check_valid_payment_status",
        ),
        # DB-level duplicate-booking guard (partial unique index supporting both SQLite and Postgres)
        db.Index(
            "ix_unique_active_booking",
            "user_id",
            "trek_id",
            unique=True,
            sqlite_where=text("status IN ('pending','booked')"),
            postgresql_where=text("status IN ('pending','booked')"),
        ),
    )

    # Relationships
    user = db.relationship("User", back_populates="bookings", lazy=True)
    trek = db.relationship("Trek", back_populates="bookings", lazy=True)
    payments = db.relationship(
        "Payment",
        back_populates="booking",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        # print(f"[DEBUG] booking {self.id} org={self.organization_id} status={self.status}")
        return f"<Booking {self.id} user_id={self.user_id} trek_id={self.trek_id} status={self.status}>"

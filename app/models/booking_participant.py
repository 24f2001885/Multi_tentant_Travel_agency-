"""
The booking_participants table — one row per person on a (group) booking (TDR v2 §4.4).
Inherits TenantScopedMixin.
"""

import uuid
import sqlalchemy as sa
from sqlalchemy import text

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class BookingParticipant(TenantScopedMixin, db.Model):
    __tablename__ = "booking_participants"

    id = db.Column(sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id = db.Column(
        db.Integer,
        db.ForeignKey("bookings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(150), nullable=False)
    age = db.Column(db.Integer, nullable=True)
    is_primary_contact = db.Column(db.Boolean, nullable=False, default=False)

    __table_args__ = (
        db.Index(
            "ix_unique_booking_primary_contact",
            "booking_id",
            unique=True,
            postgresql_where=text("is_primary_contact IS TRUE"),
            sqlite_where=text("is_primary_contact = 1"),
        ),
    )

    booking = db.relationship("Booking", back_populates="participants")

    def __repr__(self) -> str:
        # print(f"[DEBUG] participant booking={self.booking_id} primary={self.is_primary_contact}")
        return f"<BookingParticipant {self.id} booking={self.booking_id} name='{self.name}' primary={self.is_primary_contact}>"

"""
The treks table — manages trekking programs, inventory, status, and pricing (TDR v2 §4.3).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
import sqlalchemy as sa
from sqlalchemy import CheckConstraint

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class Trek(TenantScopedMixin, db.Model):
    __tablename__ = "treks"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    location = db.Column(db.String(150), nullable=False)
    difficulty = db.Column(db.String(20), nullable=False)
    duration_days = db.Column(db.Integer, nullable=False)
    total_slots = db.Column(db.Integer, nullable=False)
    available_slots = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    assigned_staff_id = db.Column(db.Integer, db.ForeignKey("staff_profiles.id"), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending")
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    thumbnail_filename = db.Column(db.String(255), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "difficulty IN ('easy', 'moderate', 'hard')",
            name="check_valid_difficulty",
        ),
        CheckConstraint("duration_days > 0", name="check_positive_duration"),
        CheckConstraint("total_slots >= 0", name="check_positive_total_slots"),
        CheckConstraint(
            "available_slots >= 0 AND available_slots <= total_slots",
            name="check_valid_available_slots",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'open', 'closed', 'completed')",
            name="check_valid_status",
        ),
    )

    # Relationships
    bookings = db.relationship("Booking", back_populates="trek", lazy=True, cascade="all, delete-orphan")
    assigned_staff = db.relationship("StaffProfile", back_populates="treks", lazy=True)

    def __repr__(self) -> str:
        # print(f"[DEBUG] {self.__class__.__name__} id={self.id} org={self.organization_id}")
        return f"<Trek {self.id} '{self.name}' status={self.status} org={self.organization_id}>"
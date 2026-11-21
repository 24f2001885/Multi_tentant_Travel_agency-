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
    location = db.Column(db.String(150), nullable=False, default="Uttarakhand")
    difficulty = db.Column(db.String(20), nullable=False, default="moderate")
    duration_days = db.Column(db.Integer, nullable=False, default=5)
    total_slots = db.Column(db.Integer, nullable=False, default=10)
    available_slots = db.Column(db.Integer, nullable=False, default=10)
    price = db.Column(db.Numeric(10, 2), nullable=False, default=1000.0)
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
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    itinerary_layout = db.Column(db.String(50), nullable=False, default="accordion")

    def __init__(self, **kwargs):
        if "title" in kwargs and "name" not in kwargs:
            kwargs["name"] = kwargs.pop("title")
        if "price_per_slot" in kwargs and "price" not in kwargs:
            kwargs["price"] = kwargs.pop("price_per_slot")
        if "location" not in kwargs or not kwargs["location"]:
            kwargs["location"] = "Uttarakhand"
        if "difficulty" not in kwargs or not kwargs["difficulty"]:
            kwargs["difficulty"] = "moderate"
        elif kwargs["difficulty"] == "difficult":
            kwargs["difficulty"] = "hard"
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        if not start_date:
            from datetime import date, timedelta
            start_date = date.today() + timedelta(days=10)
            kwargs["start_date"] = start_date
        if not end_date:
            from datetime import timedelta
            duration = kwargs.get("duration_days", 5)
            kwargs["end_date"] = start_date + timedelta(days=duration)
        if "duration_days" not in kwargs:
            days = (kwargs["end_date"] - kwargs["start_date"]).days
            kwargs["duration_days"] = max(1, days)
        if "total_slots" not in kwargs:
            kwargs["total_slots"] = kwargs.get("available_slots", 10)
        if "available_slots" not in kwargs:
            kwargs["available_slots"] = kwargs.get("total_slots", 10)
        if "price" not in kwargs:
            kwargs["price"] = 1000.0
        if "itinerary_layout" not in kwargs or not kwargs["itinerary_layout"]:
            kwargs["itinerary_layout"] = "accordion"
        super().__init__(**kwargs)

    __table_args__ = (
        CheckConstraint(
            "difficulty IN ('easy', 'moderate', 'hard')",
            name="check_valid_difficulty",
        ),
        CheckConstraint(
            "itinerary_layout IN ('accordion', 'timeline', 'magazine')",
            name="check_valid_itinerary_layout",
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

    # Property alias for compatibility
    @property
    def title(self) -> str:
        return self.name

    @title.setter
    def title(self, value: str) -> None:
        self.name = value

    @property
    def price_per_slot(self):
        return self.price

    @price_per_slot.setter
    def price_per_slot(self, value):
        self.price = value

    @sa.orm.validates("difficulty")
    def validate_difficulty(self, key, value):
        if value == "difficult":
            return "hard"
        return value

    # Relationships
    organization = db.relationship("Organization", foreign_keys="[Trek.organization_id]", lazy=True)
    bookings = db.relationship("Booking", back_populates="trek", lazy=True, cascade="all, delete-orphan")
    assigned_staff = db.relationship("StaffProfile", back_populates="treks", lazy=True)
    itinerary_days = db.relationship(
        "TrekItineraryDay",
        back_populates="trek",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="TrekItineraryDay.day_number",
    )
    media = db.relationship(
        "TrekMedia",
        back_populates="trek",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="TrekMedia.sort_order",
    )
    reviews = db.relationship(
        "TrekReview",
        back_populates="trek",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="TrekReview.created_at.desc()",
    )
    created_by = db.relationship("User", foreign_keys=[created_by_user_id], lazy=True)

    def __repr__(self) -> str:
        # print(f"[DEBUG] {self.__class__.__name__} id={self.id} org={self.organization_id}")
        return f"<Trek {self.id} '{self.name}' status={self.status} org={self.organization_id}>"
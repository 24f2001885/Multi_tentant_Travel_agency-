"""
The `trek_itinerary_days` table — one row per day of a trek's structured itinerary (TDR v2 §4.4; Milestone P10).
Inherits TenantScopedMixin.
"""

import uuid
import sqlalchemy as sa
from sqlalchemy import CheckConstraint, UniqueConstraint

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class TrekItineraryDay(TenantScopedMixin, db.Model):
    __tablename__ = "trek_itinerary_days"

    id = db.Column(sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trek_id = db.Column(
        db.Integer,
        db.ForeignKey("treks.id", ondelete="CASCADE"),
        nullable=False,
    )
    day_number = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    important_points = db.Column(db.JSON, default=list, nullable=True)

    __table_args__ = (
        CheckConstraint("day_number >= 1", name="check_itinerary_day_number_positive"),
        UniqueConstraint("trek_id", "day_number", name="uq_trek_itinerary_day_number"),
    )

    # Relationships
    trek = db.relationship("Trek", back_populates="itinerary_days")
    elements = db.relationship(
        "ItineraryElement",
        back_populates="day",
        cascade="all, delete-orphan",
        order_by="ItineraryElement.sequence",
    )

    @property
    def important_points_list(self) -> list:
        """Returns clean list of string pointers for day-level important notes."""
        if not self.important_points:
            return []
        if isinstance(self.important_points, list):
            return [str(p).strip() for p in self.important_points if str(p).strip()]
        if isinstance(self.important_points, str):
            try:
                import json
                parsed = json.loads(self.important_points)
                if isinstance(parsed, list):
                    return [str(p).strip() for p in parsed if str(p).strip()]
            except Exception:
                pass
            return [line.strip().lstrip("•-* ").strip() for line in self.important_points.splitlines() if line.strip()]
        return []

    def set_important_points(self, points) -> None:
        """Sanitizes and assigns day-level important points list."""
        if points is None:
            self.important_points = []
            return
        if isinstance(points, list):
            self.important_points = [str(p).strip().lstrip("•-* ").strip() for p in points if str(p).strip()]
        elif isinstance(points, str):
            clean_pts = []
            for line in points.splitlines():
                line = line.strip().lstrip("•-* ").strip()
                if line:
                    clean_pts.append(line)
            self.important_points = clean_pts
        else:
            self.important_points = []

    def __repr__(self) -> str:
        # print(f"[DEBUG] itinerary_day trek={self.trek_id} day={self.day_number}")
        return f"<TrekItineraryDay trek={self.trek_id} day={self.day_number} title='{self.title}'>"

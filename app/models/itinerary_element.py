"""
The `itinerary_elements` table — ordered items within an itinerary day (TDR v2 §4.4; Milestone P10).
Inherits TenantScopedMixin.
"""

import uuid
import sqlalchemy as sa
from sqlalchemy import CheckConstraint, UniqueConstraint

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class ItineraryElement(TenantScopedMixin, db.Model):
    __tablename__ = "itinerary_elements"

    id = db.Column(sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    itinerary_day_id = db.Column(
        sa.UUID(as_uuid=True),
        db.ForeignKey("trek_itinerary_days.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence = db.Column(db.Integer, nullable=False)
    element_type = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    time_of_day = db.Column(db.String(20), nullable=True)
    points = db.Column(db.JSON, default=list, nullable=True)

    day_id = sa.orm.synonym("itinerary_day_id")
    sequence_order = sa.orm.synonym("sequence")

    __table_args__ = (
        CheckConstraint("sequence >= 1", name="check_itinerary_element_sequence_positive"),
        CheckConstraint(
            "element_type IN ('activity', 'meal', 'transport', 'accommodation', 'note')",
            name="check_itinerary_element_type",
        ),
        UniqueConstraint("itinerary_day_id", "sequence", name="uq_itinerary_day_sequence"),
    )

    # Relationships
    day = db.relationship("TrekItineraryDay", back_populates="elements")

    @property
    def points_list(self) -> list:
        """Returns clean list of detail points/facts for this specific event."""
        if not self.points:
            return []
        if isinstance(self.points, list):
            return [str(p).strip() for p in self.points if str(p).strip()]
        if isinstance(self.points, str):
            try:
                import json
                parsed = json.loads(self.points)
                if isinstance(parsed, list):
                    return [str(p).strip() for p in parsed if str(p).strip()]
            except Exception:
                pass
            return [line.strip().lstrip("•-* ").strip() for line in self.points.splitlines() if line.strip()]
        return []

    def set_points(self, points) -> None:
        """Sanitizes and assigns event-level points/facts list."""
        if points is None:
            self.points = []
            return
        if isinstance(points, list):
            self.points = [str(p).strip().lstrip("•-* ").strip() for p in points if str(p).strip()]
        elif isinstance(points, str):
            clean_pts = []
            for line in points.splitlines():
                line = line.strip().lstrip("•-* ").strip()
                if line:
                    clean_pts.append(line)
            self.points = clean_pts
        else:
            self.points = []

    def __repr__(self) -> str:
        # print(f"[DEBUG] itinerary_element day={self.itinerary_day_id} seq={self.sequence} type={self.element_type}")
        return f"<ItineraryElement day={self.itinerary_day_id} seq={self.sequence} type='{self.element_type}' title='{self.title}'>"

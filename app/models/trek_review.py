"""
The `trek_reviews` table — one review per completed booking (TDR v2 §4.4; PDR v2 §5.10; Milestone P10).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
import uuid
import sqlalchemy as sa
from sqlalchemy import CheckConstraint, Index

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class TrekReview(TenantScopedMixin, db.Model):
    __tablename__ = "trek_reviews"

    id = db.Column(sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trek_id = db.Column(
        db.Integer,
        db.ForeignKey("treks.id", ondelete="CASCADE"),
        nullable=False,
    )
    booking_id = db.Column(
        db.Integer,
        db.ForeignKey("bookings.id"),
        nullable=True,
        unique=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
    )
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(500), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="check_review_rating_range"),
        Index("ix_trek_reviews_trek_created", "trek_id", "created_at"),
    )

    # Relationships
    trek = db.relationship("Trek", back_populates="reviews")
    booking = db.relationship("Booking", backref=db.backref("review", uselist=False))
    user = db.relationship("User", lazy=True)

    def __repr__(self) -> str:
        # print(f"[DEBUG] review trek={self.trek_id} booking={self.booking_id} rating={self.rating}")
        return f"<TrekReview trek={self.trek_id} user={self.user_id} rating={self.rating}>"

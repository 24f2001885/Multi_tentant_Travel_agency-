"""
The `trek_media` table — a trek's image gallery (TDR v2 §4.4; Milestone P10).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
import uuid
import sqlalchemy as sa
from sqlalchemy import Index

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class TrekMedia(TenantScopedMixin, db.Model):
    __tablename__ = "trek_media"

    id = db.Column(sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trek_id = db.Column(
        db.Integer,
        db.ForeignKey("treks.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_url = db.Column(db.String(500), nullable=False)
    caption = db.Column(db.String(255), nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_trek_media_trek_sort", "trek_id", "sort_order"),
    )

    # Relationships
    trek = db.relationship("Trek", back_populates="media")
    uploader = db.relationship("User", foreign_keys=[uploaded_by], lazy=True)

    def __repr__(self) -> str:
        # print(f"[DEBUG] trek_media trek={self.trek_id} order={self.sort_order}")
        return f"<TrekMedia trek={self.trek_id} sort={self.sort_order} url='{self.asset_url}'>"

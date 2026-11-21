"""
The waitlist_entries table (TDR v2 §4.4).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
import uuid
import sqlalchemy as sa
from sqlalchemy import CheckConstraint, Index, text

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class WaitlistEntry(TenantScopedMixin, db.Model):
    __tablename__ = "waitlist_entries"

    id = db.Column(sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trek_id = db.Column(db.Integer, db.ForeignKey("treks.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    requested_slot_count = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(20), nullable=False, default="waiting")
    offered_at = db.Column(db.DateTime(timezone=True), nullable=True)
    offer_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint("requested_slot_count >= 1", name="check_waitlist_requested_slots"),
        CheckConstraint(
            "status IN ('waiting', 'offered', 'expired', 'converted')",
            name="check_waitlist_status",
        ),
        Index(
            "ix_unique_live_waitlist_entry",
            "user_id",
            "trek_id",
            unique=True,
            postgresql_where=text("status IN ('waiting', 'offered')"),
            sqlite_where=text("status IN ('waiting', 'offered')"),
        ),
        Index("ix_waitlist_fifo_promotion", "trek_id", "status", "created_at"),
    )

    trek = db.relationship("Trek")
    user = db.relationship("User")

    def __repr__(self) -> str:
        # print(f"[DEBUG] waitlist user={self.user_id} trek={self.trek_id} status={self.status}")
        return f"<WaitlistEntry {self.id} user={self.user_id} trek={self.trek_id} status='{self.status}'>"

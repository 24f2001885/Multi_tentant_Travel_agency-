"""
The `ticket_messages` table — multi-turn message thread for support tickets (TDR v2 §4.4; Milestone P12).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
import uuid
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class TicketMessage(TenantScopedMixin, db.Model):
    __tablename__ = "ticket_messages"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id = db.Column(
        db.Integer,
        db.ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    ticket = db.relationship("Ticket", back_populates="messages")
    sender = db.relationship("User", lazy=True)

    def __repr__(self) -> str:
        return f"<TicketMessage id={self.id} ticket_id={self.ticket_id} sender_id={self.sender_id}>"

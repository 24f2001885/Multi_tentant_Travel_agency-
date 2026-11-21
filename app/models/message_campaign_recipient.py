"""
The message_campaign_recipients table — one row per REAL recipient of a sent campaign (TDR v2 §4.5).
Test sends never create rows.
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class MessageCampaignRecipient(TenantScopedMixin, db.Model):
    __tablename__ = "message_campaign_recipients"
    __table_args__ = (
        CheckConstraint(
            "delivery_status IN ('queued', 'sent', 'delivered', 'read', 'failed')",
            name="check_recipient_delivery_status",
        ),
        UniqueConstraint("campaign_id", "user_id", name="uq_campaign_recipient_user"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("message_campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    delivery_status = db.Column(String(20), nullable=False, default="queued")
    sent_at = db.Column(DateTime(timezone=True), nullable=True)
    provider_message_id = db.Column(String(100), nullable=True, index=True)

    # Relationships
    campaign = db.relationship("MessageCampaign", back_populates="recipients")
    user = db.relationship("User", backref=db.backref("received_campaigns", lazy=True))

    def __repr__(self) -> str:
        return f"<MessageCampaignRecipient campaign={self.campaign_id} user={self.user_id} status={self.delivery_status}>"

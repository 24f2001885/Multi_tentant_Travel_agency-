"""
The message_campaigns table — a composed message + audience (TDR v2 §4.5).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class MessageCampaign(TenantScopedMixin, db.Model):
    __tablename__ = "message_campaigns"
    __table_args__ = (
        CheckConstraint("channel IN ('email', 'whatsapp', 'sms')", name="check_campaign_valid_channel"),
        CheckConstraint("status IN ('draft', 'tested', 'sent')", name="check_campaign_valid_status"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_by = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    channel = db.Column(String(10), nullable=False)
    subject = db.Column(String(200), nullable=True)
    template_body = db.Column(Text, nullable=False)
    poster_url = db.Column(String(500), nullable=True)
    audience_filter = db.Column(db.JSON, nullable=False, default=dict)
    status = db.Column(String(10), nullable=False, default="draft")
    test_sent_to_email = db.Column(String(255), nullable=True)
    test_sent_to_mobile = db.Column(String(30), nullable=True)
    sent_at = db.Column(DateTime(timezone=True), nullable=True)
    recipient_count = db.Column(Integer, nullable=True)
    provider_template_name = db.Column(String(100), nullable=True)
    created_at = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    organization = db.relationship("Organization", foreign_keys="[MessageCampaign.organization_id]", lazy=True)
    creator = db.relationship("User", foreign_keys=[created_by], backref=db.backref("created_campaigns", lazy=True))
    recipients = db.relationship(
        "MessageCampaignRecipient",
        back_populates="campaign",
        cascade="all, delete-orphan",
        lazy=True,
    )

    def __repr__(self) -> str:
        return f"<MessageCampaign {self.id} channel={self.channel} status={self.status}>"

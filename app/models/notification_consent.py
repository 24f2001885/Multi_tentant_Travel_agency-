"""
The notification_consents table — timestamped, per-user, per-channel, per-type consent records (TDR v2 §4.5; PDR v2 §5.11).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class NotificationConsent(TenantScopedMixin, db.Model):
    __tablename__ = "notification_consents"
    __table_args__ = (
        CheckConstraint("channel IN ('email', 'whatsapp', 'sms')", name="check_consent_valid_channel"),
        CheckConstraint("consent_type IN ('transactional', 'marketing')", name="check_consent_valid_type"),
        UniqueConstraint("user_id", "channel", "consent_type", name="uq_notification_consents_user_channel_type"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    channel = db.Column(String(10), nullable=False)
    consent_type = db.Column(String(15), nullable=False)
    granted_at = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    revoked_at = db.Column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = db.relationship("User", backref=db.backref("consents", lazy=True, cascade="all, delete-orphan"))

    @property
    def is_active(self) -> bool:
        """Consent is active when revoked_at is None."""
        return self.revoked_at is None

    def __repr__(self) -> str:
        return f"<NotificationConsent user={self.user_id} {self.channel}/{self.consent_type} active={self.is_active}>"

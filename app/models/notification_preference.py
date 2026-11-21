"""
The notification_preferences table — per-user per-channel on/off switch (TDR v2 §4.5).
Inherits TenantScopedMixin.
"""

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class NotificationPreference(TenantScopedMixin, db.Model):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        CheckConstraint("channel IN ('email', 'whatsapp', 'sms')", name="check_preference_valid_channel"),
        UniqueConstraint("user_id", "channel", name="uq_notification_preferences_user_channel"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    channel = db.Column(String(10), nullable=False)
    is_enabled = db.Column(Boolean, nullable=False, default=True)

    # Relationships
    user = db.relationship("User", backref=db.backref("notification_preferences", lazy=True, cascade="all, delete-orphan"))

    def __repr__(self) -> str:
        return f"<NotificationPreference user={self.user_id} {self.channel} enabled={self.is_enabled}>"

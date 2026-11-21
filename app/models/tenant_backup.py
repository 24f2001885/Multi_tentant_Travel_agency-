"""
The tenant_backups table — self-serve tenant data exports and platform disaster recovery archives (TDR v2 §4.6; Milestone P13).
"""

from datetime import datetime, timedelta, timezone
import uuid
import sqlalchemy as sa
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class TenantBackup(db.Model):
    __tablename__ = "tenant_backups"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,  # NULL indicates full platform backup
        index=True,
    )
    triggered_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    triggered_by_platform_admin_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("platform_admins.id", ondelete="SET NULL"),
        nullable=True,
    )
    storage_location = db.Column(db.String(500), nullable=False)
    file_size_bytes = db.Column(db.BigInteger, nullable=True)
    status = db.Column(db.String(10), nullable=False, default="pending")
    backup_type = db.Column(db.String(20), nullable=False, default="tenant_export")
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="check_valid_backup_status",
        ),
        CheckConstraint(
            "backup_type IN ('tenant_export', 'full_platform')",
            name="check_valid_backup_type",
        ),
    )

    organization = db.relationship("Organization", lazy=True)
    triggered_by_user = db.relationship("User", foreign_keys=[triggered_by_user_id], lazy=True)
    triggered_by_platform_admin = db.relationship(
        "PlatformAdmin", foreign_keys=[triggered_by_platform_admin_id], lazy=True
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc)
        if not self.expires_at:
            # Default 30-day retention window
            self.expires_at = self.created_at + timedelta(days=30)

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at

    def __repr__(self) -> str:
        return f"<TenantBackup id={self.id} org={self.organization_id} type={self.backup_type} status={self.status}>"

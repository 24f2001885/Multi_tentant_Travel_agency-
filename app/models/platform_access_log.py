"""
The `platform_access_logs` meta-audit trail — who at platform level read which
tenant's data, and when (TDR v2 §4.6). Separate from audit_logs on purpose.
NOT tenant-scoped (organization_id here names the tenant whose data was accessed;
it is a plain column, not the mixin).
"""

from datetime import datetime, timezone
from typing import Any
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class PlatformAccessLog(db.Model):
    __tablename__ = "platform_access_logs"

    id = db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    platform_admin_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("platform_admins.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    action = db.Column(db.String(100), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    platform_admin = db.relationship("PlatformAdmin", backref=db.backref("access_logs", lazy="dynamic"))
    organization = db.relationship("Organization")

    @classmethod
    def record(cls, platform_admin_id: uuid.UUID, organization_id: uuid.UUID, action: str) -> "PlatformAccessLog":
        """
        Builds and registers a new access log entry in the current session WITHOUT committing.
        The caller must commit this in the same transaction as the logged cross-tenant read.
        """
        entry = cls(
            platform_admin_id=platform_admin_id,
            organization_id=organization_id,
            action=action,
        )
        db.session.add(entry)
        return entry

    def __repr__(self) -> str:
        # print(f"[DEBUG] platform_access {self.platform_admin_id} -> org {self.organization_id} {self.action}")
        return f"<PlatformAccessLog admin={self.platform_admin_id} org={self.organization_id} action={self.action}>"

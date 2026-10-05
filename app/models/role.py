"""
Tenant-defined roles and the role_capabilities join table (TDR v2 §4.2).
"""

from datetime import datetime, timezone
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


role_capabilities = db.Table(
    "role_capabilities",
    db.metadata,
    db.Column(
        "role_id",
        UUID(as_uuid=True),
        db.ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "capability_id",
        UUID(as_uuid=True),
        db.ForeignKey("capabilities.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Role(TenantScopedMixin, db.Model):
    __tablename__ = "roles"

    id = db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name = db.Column(db.String(100), nullable=False)
    is_starter_template = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        db.UniqueConstraint("organization_id", "name", name="uq_roles_org_name"),
    )

    capabilities = db.relationship(
        "Capability",
        secondary=role_capabilities,
        backref=db.backref("roles", lazy="dynamic"),
        lazy="select",
    )

    staff_profiles = db.relationship(
        "StaffProfile",
        back_populates="role",
        lazy="dynamic",
    )

    def __repr__(self) -> str:
        # print(f"[DEBUG] role {self.name} org={self.organization_id} caps={len(self.capabilities)}")
        return f"<Role {self.name} org={self.organization_id} starter={self.is_starter_template}>"

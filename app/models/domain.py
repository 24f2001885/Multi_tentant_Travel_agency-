"""
The domains table — subdomain/custom-domain to organization map (TDR v2 §4.1).
NOT tenant-scoped.
"""

from datetime import datetime
from typing import Optional
import uuid

import sqlalchemy as sa
from sqlalchemy import CheckConstraint
from sqlalchemy.orm import validates

from app.extensions import db


class Domain(db.Model):
    __tablename__ = "domains"
    __table_args__ = (
        CheckConstraint("domain_type IN ('subdomain', 'custom')", name="check_domain_type"),
    )

    id = db.Column(
        sa.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    organization_id = db.Column(
        sa.UUID(as_uuid=True),
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    domain_name = db.Column(db.String(255), nullable=False, unique=True, index=True)
    domain_type = db.Column(db.String(20), nullable=False)
    is_primary = db.Column(db.Boolean, nullable=False, default=True)
    verified_at = db.Column(db.DateTime(timezone=True), nullable=True)

    @validates("domain_name")
    def validate_domain_name(self, key: str, value: str) -> str:
        if not value:
            raise ValueError("Domain name cannot be empty")
        return value.strip().lower()

    @property
    def is_routable(self) -> bool:
        """Determines if requests to this domain can be routed to the tenant."""
        # print(f"[DEBUG] domain {self.domain_name} type={self.domain_type} routable={self.is_routable}")
        if self.domain_type == "subdomain":
            return True
        return self.verified_at is not None

    def __repr__(self) -> str:
        return f"<Domain {self.domain_name} ({self.domain_type})>"

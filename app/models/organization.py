"""
The organizations table — one row per tenant, carrying branding, legal text, and payout linkage (TDR v2 §4.1).
NOT tenant-scoped (it IS the tenant).
"""

from datetime import datetime, timezone
from typing import Any, Dict
import uuid

import sqlalchemy as sa
from sqlalchemy import CheckConstraint

from app.extensions import db


class Organization(db.Model):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended')", name="check_org_status"),
    )

    id = db.Column(
        sa.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name = db.Column(db.String(150), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="active")
    logo_url = db.Column(db.String(500), nullable=True)
    primary_color = db.Column(db.String(7), nullable=True)
    secondary_color = db.Column(db.String(7), nullable=True)
    contact_email = db.Column(db.String(255), nullable=True)
    contact_phone = db.Column(db.String(20), nullable=True)
    legal_terms_text = db.Column(db.Text, nullable=True)
    legal_privacy_text = db.Column(db.Text, nullable=True)
    gst_number = db.Column(db.String(20), nullable=True)
    razorpay_route_account_id = db.Column(db.String(100), nullable=True)

    # Circular reference with users.id resolved via use_alter=True
    tenant_admin_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", use_alter=True, name="fk_org_tenant_admin_user"),
        nullable=True,
    )
    created_by_platform_admin_id = db.Column(
        sa.UUID(as_uuid=True),
        nullable=True,
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    domains = db.relationship(
        "Domain",
        backref="organization",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def branding(self) -> Dict[str, Any]:
        """Returns the branding tokens and contact details for UI rendering."""
        # print(f"[DEBUG] organization {self.id} name={self.name} status={self.status}")
        return {
            "name": self.name,
            "logo_url": self.logo_url,
            "primary_color": self.primary_color,
            "secondary_color": self.secondary_color,
            "contact_email": self.contact_email,
            "contact_phone": self.contact_phone,
            "legal_terms_text": self.legal_terms_text,
            "legal_privacy_text": self.legal_privacy_text,
        }

    def __repr__(self) -> str:
        return f"<Organization {self.name} [{self.status}]>"

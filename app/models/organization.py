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


def _default_enterprise_features():
    return [
        "campaigns",
        "coupons",
        "custom_themes",
        "advanced_itineraries",
        "manual_bookings",
        "custom_domain",
        "backups",
        "refund_policy",
    ]


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
    manual_booking_enabled = db.Column(db.Boolean, nullable=False, default=False)
    last_invoice_number = db.Column(db.Integer, nullable=False, default=0)
    theme = db.Column(db.String(100), nullable=True, default=None)
    subscription_tier = db.Column(db.String(50), nullable=False, server_default="enterprise", default="enterprise")
    features = db.Column(sa.JSON, nullable=False, server_default="[]", default=_default_enterprise_features)

    # Circular reference with users.id resolved via use_alter=True
    tenant_admin_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", use_alter=True, name="fk_org_tenant_admin_user"),
        nullable=True,
    )
    created_by_platform_admin_id = db.Column(
        sa.UUID(as_uuid=True),
        db.ForeignKey("platform_admins.id", name="fk_org_created_by_platform_admin"),
        nullable=False,
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    tenant_admin = db.relationship(
        "User",
        foreign_keys=[tenant_admin_user_id],
        viewonly=True,
    )
    domains = db.relationship(
        "Domain",
        backref="organization",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def branding(self) -> Dict[str, Any]:
        """Returns the branding tokens and contact details for UI rendering."""
        # print(f"[DEBUG] org {self.id} created_by={self.created_by_platform_admin_id}")
        # print(f"[DEBUG] organization {self.id} name={self.name} status={self.status}")
        return {
            "id": self.id,
            "name": self.name,
            "theme": self.theme,
            "subscription_tier": self.subscription_tier,
            "features": self.features,
            "has_feature": self.has_feature,
            "logo_url": self.logo_url,
            "primary_color": self.primary_color,
            "secondary_color": self.secondary_color,
            "contact_email": self.contact_email,
            "contact_phone": self.contact_phone,
            "legal_terms_text": self.legal_terms_text,
            "legal_privacy_text": self.legal_privacy_text,
        }

    @property
    def is_route_verified(self) -> bool:
        """True when razorpay_route_account_id is set."""
        # print(f"[DEBUG] org {self.id} manual={self.manual_booking_enabled} route_ok={bool(self.razorpay_route_account_id)}")
        return bool(self.razorpay_route_account_id and self.razorpay_route_account_id.strip())

    def has_feature(self, feature_key: str) -> bool:
        """Checks if a feature is enabled on this tenant's subscription."""
        from app.services.tenant_features_service import is_feature_enabled
        return is_feature_enabled(self, feature_key)

    @property
    def subscription_tier_label(self) -> str:
        from app.services.tenant_features_service import SUBSCRIPTION_TIERS
        tier_info = SUBSCRIPTION_TIERS.get((self.subscription_tier or "enterprise").lower())
        return tier_info["name"] if tier_info else (self.subscription_tier or "Enterprise").capitalize()

    def __repr__(self) -> str:
        return f"<Organization {self.name} [{self.status}]>"


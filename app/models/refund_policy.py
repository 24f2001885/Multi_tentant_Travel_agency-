"""
The refund_policies table — a tenant's tiered refund rules (TDR v2 §4.4; PDR v2 §5.9).
Inherits TenantScopedMixin.
"""

import uuid
import sqlalchemy as sa
from sqlalchemy import CheckConstraint, Index

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class RefundPolicy(TenantScopedMixin, db.Model):
    __tablename__ = "refund_policies"

    id = db.Column(sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    min_days_before_trip = db.Column(db.Integer, nullable=False)
    max_days_before_trip = db.Column(db.Integer, nullable=True)
    refund_percentage = db.Column(db.Numeric(5, 2), nullable=False)

    __table_args__ = (
        CheckConstraint("min_days_before_trip >= 0", name="check_refund_policy_min_days"),
        CheckConstraint(
            "max_days_before_trip IS NULL OR max_days_before_trip >= min_days_before_trip",
            name="check_refund_policy_max_gte_min",
        ),
        CheckConstraint(
            "refund_percentage >= 0 AND refund_percentage <= 100",
            name="check_refund_policy_percentage",
        ),
        Index("ix_refund_policies_org_min_days", "organization_id", "min_days_before_trip"),
    )

    def __repr__(self) -> str:
        # print(f"[DEBUG] refund_policy {self.min_days_before_trip}-{self.max_days_before_trip} => {self.refund_percentage}%")
        return f"<RefundPolicy {self.min_days_before_trip}-{self.max_days_before_trip} => {self.refund_percentage}%>"

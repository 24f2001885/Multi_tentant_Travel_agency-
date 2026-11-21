"""
The coupons table — tenant discount codes (TDR v2 §4.4).
Inherits TenantScopedMixin.
"""

import uuid
import sqlalchemy as sa
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.orm import validates

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class Coupon(TenantScopedMixin, db.Model):
    __tablename__ = "coupons"

    id = db.Column(sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = db.Column(db.String(30), nullable=False)
    discount_type = db.Column(db.String(10), nullable=False)
    discount_value = db.Column(db.Numeric(10, 2), nullable=False)
    valid_from = db.Column(db.Date, nullable=True)
    valid_until = db.Column(db.Date, nullable=True)
    max_uses = db.Column(db.Integer, nullable=True)
    used_count = db.Column(db.Integer, nullable=False, default=0)
    min_booking_amount = db.Column(db.Numeric(10, 2), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_coupon_org_code"),
        CheckConstraint("discount_type IN ('percentage', 'flat')", name="check_coupon_discount_type"),
        CheckConstraint("discount_value > 0", name="check_coupon_discount_positive"),
        CheckConstraint(
            "discount_type != 'percentage' OR discount_value <= 100",
            name="check_coupon_percentage_limit",
        ),
    )

    redemptions = db.relationship("CouponRedemption", back_populates="coupon", cascade="all, delete-orphan")

    @property
    def effective_status(self) -> str:
        if not self.is_active:
            return "inactive"
        from datetime import date
        today = date.today()
        if self.valid_from and today < self.valid_from:
            return "scheduled"
        if self.valid_until and today > self.valid_until:
            return "expired"
        if self.max_uses is not None and self.used_count >= self.max_uses:
            return "exhausted"
        return "active"

    @validates("code")
    def validate_code(self, key: str, value: str) -> str:
        if value:
            return value.strip().upper()
        return value

    def __repr__(self) -> str:
        # print(f"[DEBUG] coupon {self.code} type={self.discount_type} used={self.used_count}/{self.max_uses}")
        return f"<Coupon {self.code} type={self.discount_type} used={self.used_count}/{self.max_uses}>"

"""
The coupon_redemptions table — one row per redeemed coupon (TDR v2 §4.4).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
import uuid
import sqlalchemy as sa
from sqlalchemy import UniqueConstraint

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class CouponRedemption(TenantScopedMixin, db.Model):
    __tablename__ = "coupon_redemptions"

    id = db.Column(sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    coupon_id = db.Column(sa.UUID(as_uuid=True), db.ForeignKey("coupons.id"), nullable=False, index=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), nullable=False, unique=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    discount_applied = db.Column(db.Numeric(10, 2), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("booking_id", name="uq_coupon_redemption_booking"),
    )

    coupon = db.relationship("Coupon", back_populates="redemptions")
    booking = db.relationship("Booking", backref=db.backref("coupon_redemption", uselist=False))
    user = db.relationship("User")

    def __repr__(self) -> str:
        # print(f"[DEBUG] redemption coupon={self.coupon_id} booking={self.booking_id} discount={self.discount_applied}")
        return f"<CouponRedemption {self.id} coupon={self.coupon_id} booking={self.booking_id} discount={self.discount_applied}>"

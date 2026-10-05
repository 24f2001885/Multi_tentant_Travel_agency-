"""
The staff_profiles table — holds coordinator/staff details and approval state (TDR v2 §4.3).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
import sqlalchemy as sa
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class StaffProfile(TenantScopedMixin, db.Model):
    __tablename__ = "staff_profiles"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    role_id = db.Column(UUID(as_uuid=True), db.ForeignKey("roles.id", ondelete="SET NULL"), nullable=True)
    contact_details = db.Column(db.String(255), nullable=True)
    approval_status = db.Column(db.String(20), nullable=False, default="pending")
    approved_by_admin_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approved_at = db.Column(db.DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "approval_status IN ('pending', 'approved', 'blacklisted')",
            name="check_valid_approval_status",
        ),
    )

    user = db.relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="staff_profile",
    )
    approved_by_admin = db.relationship(
        "User",
        foreign_keys=[approved_by_admin_id],
    )
    treks = db.relationship(
        "Trek",
        back_populates="assigned_staff",
        lazy=True,
    )
    role = db.relationship(
        "Role",
        back_populates="staff_profiles",
    )

    def __repr__(self) -> str:
        # print(f"[DEBUG] staff_profile {self.id} role_id={self.role_id}")
        # print(f"[DEBUG] {self.__class__.__name__} id={self.id} org={self.organization_id}")
        return f"<StaffProfile {self.id} user_id={self.user_id} approval_status={self.approval_status}>"
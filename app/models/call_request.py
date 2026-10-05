"""
The call_requests table — footer 'Request a Call' submissions (TDR v2 §4.5; PDR v2 §5.13).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
import uuid

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, Index

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class CallRequest(TenantScopedMixin, db.Model):
    __tablename__ = "call_requests"
    __table_args__ = (
        CheckConstraint("status IN ('new', 'contacted', 'closed')", name="check_call_request_status"),
        Index("ix_call_requests_org_status_created", "organization_id", "status", "created_at"),
    )

    id = db.Column(
        sa.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    preferred_time = db.Column(db.String(100), nullable=True)
    message = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(10), nullable=False, default="new")
    handled_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    handler = db.relationship("User", foreign_keys=[handled_by], backref="handled_call_requests")

    def __repr__(self) -> str:
        # print(f"[DEBUG] call_request {self.id} status={self.status}")
        return f"<CallRequest {self.name} [{self.status}]>"

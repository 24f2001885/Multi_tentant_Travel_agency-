"""
The audit_logs table — records security and operational actions within a tenant (TDR v2 §4.3).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
from typing import Optional
import sqlalchemy as sa

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class AuditLog(TenantScopedMixin, db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    action = db.Column(db.String(100), nullable=False)
    target_type = db.Column(db.String(50), nullable=False, default="system", server_default="system")
    target_id = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    actor_id = sa.orm.synonym("actor_user_id")
    user_id = sa.orm.synonym("actor_user_id")

    actor = db.relationship("User", lazy=True)

    @classmethod
    def record(
        cls,
        actor_id: int,
        action: str,
        target_type: str,
        target_id: int,
        details: Optional[str] = None,
    ) -> "AuditLog":
        """
        Builds and returns an AuditLog instance for the specified action.
        Does NOT commit — caller manages transaction.
        """
        return cls(
            actor_user_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )

    def __repr__(self) -> str:
        # print(f"[DEBUG] {self.__class__.__name__} id={self.id} org={self.organization_id}")
        return (
            f"<AuditLog {self.id} actor_user_id={self.actor_user_id} "
            f"action='{self.action}' target={self.target_type}:{self.target_id} org={self.organization_id}>"
        )
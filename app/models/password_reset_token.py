"""
The password_reset_tokens table — stores single-use hashed password reset tokens (TDR v2 §4.3).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
import sqlalchemy as sa

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin


class PasswordResetToken(TenantScopedMixin, db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    token_hash = db.Column(db.String(255), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    user = db.relationship("User", lazy=True)

    def is_valid(self) -> bool:
        now = datetime.now(timezone.utc)
        expires = self.expires_at
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return self.used_at is None and expires > now

    def __repr__(self) -> str:
        # print(f"[DEBUG] {self.__class__.__name__} id={self.id} org={self.organization_id}")
        return f"<PasswordResetToken {self.id} user_id={self.user_id} org={self.organization_id}>"
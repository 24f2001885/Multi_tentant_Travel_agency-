"""
The `platform_admins` table — the platform operator's own account, structurally
outside every tenant (TDR v2 §4.1). NOT tenant-scoped, no FK into any tenant table.
"""

from datetime import datetime, timezone
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


class PlatformAdmin(db.Model):
    __tablename__ = "platform_admins"

    id = db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def __repr__(self) -> str:
        # print(f"[DEBUG] platform_admin {self.id} active={self.is_active}")
        return f"<PlatformAdmin {self.email} [active={self.is_active}]>"

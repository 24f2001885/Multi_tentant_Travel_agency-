"""
The global capabilities catalog (TDR v2 §4.2). NOT tenant-scoped.
"""

from typing import Any
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class Capability(db.Model):
    __tablename__ = "capabilities"

    id = db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    key = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=False)
    is_protected = db.Column(db.Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        # print(f"[DEBUG] capability {self.key} protected={self.is_protected}")
        return f"<Capability key={self.key} is_protected={self.is_protected}>"

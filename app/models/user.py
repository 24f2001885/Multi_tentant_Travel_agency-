"""
The users table — handles authentication, credentials, and profile state (TDR v2 §4.3; PDR v2 §5.5).
Inherits TenantScopedMixin.
"""

from datetime import datetime, timezone
from typing import Optional

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.orm import validates
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.tenancy.scoping import TenantScopedMixin

LEGACY_MOBILE_SENTINEL = "UNSET"


class User(TenantScopedMixin, db.Model, UserMixin):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'staff', 'trekker')", name="check_valid_role"),
        UniqueConstraint("organization_id", "email", name="uq_users_org_email"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    must_reset_password = db.Column(db.Boolean, nullable=False, default=False)

    # P7 Compliance fields
    mobile_country_code = db.Column(db.String(5), nullable=False, default="+91")
    mobile_number = db.Column(db.String(20), nullable=False, default=LEGACY_MOBILE_SENTINEL)
    terms_accepted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    profile_picture_filename = db.Column(db.String(255), nullable=True, default=None)

    # Relationships
    bookings = db.relationship(
        "Booking",
        back_populates="user",
        lazy=True,
        cascade="all, delete-orphan",
    )
    staff_profile = db.relationship(
        "StaffProfile",
        back_populates="user",
        foreign_keys="StaffProfile.user_id",
        uselist=False,
        lazy=True,
        cascade="all, delete-orphan",
    )
    tickets = db.relationship(
        "Ticket",
        back_populates="user",
        foreign_keys="Ticket.user_id",
        lazy=True,
        cascade="all, delete-orphan",
    )

    @validates("email")
    def validate_email(self, key: str, value: str) -> str:
        if not value:
            raise ValueError("Email cannot be empty")
        return value.strip().lower()

    @property
    def needs_account_completion(self) -> bool:
        """Determines if a user must complete mobile or terms registration before accessing the application."""
        # print(f"[DEBUG] user {self.id} org={self.organization_id} needs_completion={self.needs_account_completion}")
        return self.terms_accepted_at is None or self.mobile_number == LEGACY_MOBILE_SENTINEL

    def set_password(self, raw_password: str) -> None:
        """Hashes the password and stores it."""
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        """Verifies a plaintext password against the stored hash."""
        return check_password_hash(self.password_hash, raw_password)

    def get_id(self) -> str:
        return str(self.id)

    @property
    def has_profile_picture(self) -> bool:
        return self.profile_picture_filename is not None

    def __repr__(self) -> str:
        return f"<User {self.id} {self.email} [{self.role}] org={self.organization_id}>"

from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy import CheckConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    must_reset_password = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    profile_picture_filename = db.Column(db.String(255), nullable=True, default=None)

    __table_args__ = (
        db.CheckConstraint(
            role.in_(['admin', 'staff', 'trekker']),
            name='check_valid_role'
        ),
    )

    bookings = db.relationship('Booking', back_populates='user', lazy=True, cascade="all, delete-orphan")
    staff_profile = db.relationship(
        'StaffProfile',
        back_populates='user',
        foreign_keys='StaffProfile.user_id',
        uselist=False,
        lazy=True,
        cascade="all, delete-orphan"
    )
    tickets = db.relationship(
        'Ticket',
        back_populates='user',
        foreign_keys='Ticket.user_id',
        lazy=True,
        cascade="all, delete-orphan"
    )

    def set_password(self, raw_password: str) -> None:
        """Hashes the password and stores it."""
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        """Verifies a plaintext password against the stored hash."""
        return check_password_hash(self.password_hash, raw_password)

    def get_id(self):
       
        return str(self.id)

    @property
    def has_profile_picture(self):
       
        return self.profile_picture_filename is not None

    
    def __repr__(self):
        return f"<User {self.id} {self.email} {self.role}>"
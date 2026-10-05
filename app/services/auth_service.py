
import hashlib
import secrets
from datetime import datetime, timedelta

from flask import current_app

from app.extensions import db
from app.models.password_reset_token import PasswordResetToken
from app.models.staff_profile import StaffProfile
from app.models.user import User
from app.services import notification_service

class DuplicateEmailError(Exception):
    """Raised when registering with an email that already exists."""

def register_user(name, email, password, role, contact_details=None):
    if User.query.filter_by(email=email).first() is not None:
        raise DuplicateEmailError(f"Email already registered: {email}")

    user = User(name=name, email=email, role=role)
    user.set_password(password)
    db.session.add(user)

    try:
        db.session.flush()
        if role == "staff":
            staff_profile = StaffProfile(
                user_id=user.id,
                contact_details=contact_details,
                approval_status="pending",
            )
            db.session.add(staff_profile)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return user

def authenticate(email, password):
    user = User.query.filter_by(email=email).first()
    if user is None or not user.check_password(password):
        user = None
    return user

def request_password_reset(email):
    user = User.query.filter_by(email=email).first()
    if user is None:
        return

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expiry_minutes = current_app.config.get("RESET_TOKEN_EXPIRY_MINUTES", 30)

    reset_token = PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.utcnow() + timedelta(minutes=expiry_minutes),
    )
    db.session.add(reset_token)
    db.session.commit()

    notification_service.send_otp_reset_email(user, raw_token)

def reset_password(raw_token, new_password):
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    reset_token = PasswordResetToken.query.filter_by(token_hash=token_hash).first()

    if reset_token is None or not reset_token.is_valid():
        return False

    user = User.query.get(reset_token.user_id)
    user.set_password(new_password)
    reset_token.used_at = datetime.utcnow()
    db.session.commit()
    return True

def issue_staff_default_password(staff_user):
    default_password = f"{staff_user.email.split('@')[0]}@pass"
    staff_user.set_password(default_password)
    staff_user.must_reset_password = True
    return default_password
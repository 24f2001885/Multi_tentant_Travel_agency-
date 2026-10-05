"""
Authentication and account lifecycle management service (TDR v2 §5.5, §6).
"""

from datetime import datetime, timedelta, timezone
import hashlib
import os
import secrets
from typing import Optional, Tuple

from flask import current_app, g
import phonenumbers

from app.extensions import db
from app.models.password_reset_token import PasswordResetToken
from app.models.staff_profile import StaffProfile
from app.models.user import User
from app.services import notification_service


class DuplicateEmailError(Exception):
    """Raised when registering with an email that already exists within the tenant."""
    pass


class InvalidMobileError(Exception):
    """Raised when mobile number format is invalid according to E.164 / country standards."""
    pass


class TermsNotAcceptedError(Exception):
    """Raised when mandatory terms and conditions acceptance is missing."""
    pass


def validate_mobile(country_code: str, number: str) -> Tuple[str, str, str]:
    """
    Validates a mobile number using the phonenumbers library.
    Returns (normalized_country_code, national_number, e164_formatted_string).
    """
    raw = (f"{country_code.strip()} {number.strip()}").strip()
    default_region = os.environ.get("DEFAULT_MOBILE_REGION", "IN")
    if current_app:
        default_region = current_app.config.get("DEFAULT_MOBILE_REGION", default_region)

    try:
        parsed = phonenumbers.parse(raw, default_region)
        if not phonenumbers.is_valid_number(parsed):
            raise InvalidMobileError(f"Invalid mobile number: {number}")
        cc = f"+{parsed.country_code}"
        national = str(parsed.national_number)
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        return cc, national, e164
    except Exception as e:
        if isinstance(e, InvalidMobileError):
            raise
        raise InvalidMobileError(f"Could not parse phone number '{raw}': {str(e)}")


def register_user(
    name: str,
    email: str,
    password: str,
    role: str,
    contact_details: Optional[str] = None,
    mobile_country_code: str = "+91",
    mobile_number: Optional[str] = None,
    accept_terms: bool = True,
) -> User:
    """
    Registers a new user in the current tenant context.
    Captures mobile country code, national number, and terms acceptance timestamp.
    """
    if not accept_terms:
        raise TermsNotAcceptedError("You must accept the Terms and Conditions and Privacy Policy.")

    if not mobile_number:
        raise InvalidMobileError("Mobile number is required.")

    cc, nat, e164 = validate_mobile(mobile_country_code, mobile_number)

    # Scoped query ensures email uniqueness is enforced per-organization
    if User.query.filter_by(email=email.strip().lower()).first() is not None:
        raise DuplicateEmailError(f"Email already registered: {email}")

    user = User(
        name=name.strip(),
        email=email.strip().lower(),
        role=role,
        mobile_country_code=cc,
        mobile_number=nat,
        terms_accepted_at=datetime.now(timezone.utc),
    )
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

    # print(f"[DEBUG] register_user org={getattr(g,'organization_id',None)} e164={e164}")
    return user


def complete_account(
    user: User,
    country_code: str,
    number: str,
    accept_terms: bool = True,
) -> User:
    """
    Completes registration requirements for legacy or staff-created accounts.
    """
    if not accept_terms:
        raise TermsNotAcceptedError("You must accept the Terms and Conditions and Privacy Policy.")

    cc, nat, _ = validate_mobile(country_code, number)
    user.mobile_country_code = cc
    user.mobile_number = nat
    user.terms_accepted_at = datetime.now(timezone.utc)
    db.session.commit()
    return user


def authenticate(email: str, password: str) -> Optional[User]:
    """Authenticates a user within the active tenant."""
    user = User.query.filter_by(email=email.strip().lower()).first()
    if user is None or not user.check_password(password):
        return None
    return user


def request_password_reset(email: str) -> None:
    """Generates a password reset token for the specified user within the active tenant."""
    user = User.query.filter_by(email=email.strip().lower()).first()
    if user is None:
        return

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expiry_minutes = current_app.config.get("RESET_TOKEN_EXPIRY_MINUTES", 30)

    reset_token = PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes),
    )
    db.session.add(reset_token)
    db.session.commit()

    notification_service.send_otp_reset_email(user, raw_token)


def reset_password(raw_token: str, new_password: str) -> bool:
    """Validates token and updates user password."""
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    reset_token = PasswordResetToken.query.filter_by(token_hash=token_hash).first()

    if reset_token is None or not reset_token.is_valid():
        return False

    user = User.query.get(reset_token.user_id)
    if user is None:
        return False

    user.set_password(new_password)
    reset_token.used_at = datetime.now(timezone.utc)
    db.session.commit()
    return True


def issue_staff_default_password(staff_user: User) -> str:
    """Generates a temporary default password for staff members requiring a reset on first login."""
    default_password = f"{staff_user.email.split('@')[0]}@pass"
    staff_user.set_password(default_password)
    staff_user.must_reset_password = True
    return default_password
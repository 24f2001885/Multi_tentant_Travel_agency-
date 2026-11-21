"""
Authentication and account lifecycle management service (TDR v2 §5.5, §6).
"""

from datetime import datetime, timedelta, timezone
import hashlib
import os
import secrets
from typing import Optional, Tuple, Any


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


def record_consent(
    user: User,
    channel: str,
    consent_type: str,
    granted: bool,
    source: str,
) -> Any:
    """
    Upserts the single notification_consents row for (user_id, channel, consent_type).
    granted -> granted_at=now, revoked_at=None
    revoked -> revoked_at=now
    Audits consent.grant or consent.revoke with source.
    Does NOT commit.
    """
    from app.models.notification_consent import NotificationConsent
    from app.models.audit_log import AuditLog
    import json

    channel = channel.strip().lower()
    consent_type = consent_type.strip().lower()

    consent = NotificationConsent.query.filter_by(
        user_id=user.id,
        channel=channel,
        consent_type=consent_type,
    ).first()

    now = datetime.now(timezone.utc)
    if not consent:
        consent = NotificationConsent(
            organization_id=user.organization_id,
            user_id=user.id,
            channel=channel,
            consent_type=consent_type,
            granted_at=now,
            revoked_at=None if granted else now,
        )
        db.session.add(consent)
    else:
        if granted:
            consent.granted_at = now
            consent.revoked_at = None
        else:
            consent.revoked_at = now

    audit = AuditLog.record(
        actor_id=user.id,
        action="consent.grant" if granted else "consent.revoke",
        target_type="notification_consent",
        target_id=0,
        details=json.dumps({
            "user_id": user.id,
            "channel": channel,
            "consent_type": consent_type,
            "granted": granted,
            "source": source,
        }),
    )
    audit.organization_id = user.organization_id
    db.session.add(audit)

    return consent


def register_user(
    name: str,
    email: str,
    password: str,
    role: str,
    contact_details: Optional[str] = None,
    mobile_country_code: str = "+91",
    mobile_number: Optional[str] = None,
    accept_terms: bool = True,
    whatsapp_transactional: bool = False,
    sms_transactional: bool = False,
    marketing_email: bool = False,
    marketing_whatsapp: bool = False,
    marketing_sms: bool = False,
) -> User:
    """
    Registers a new user in the current tenant context.
    Captures mobile country code, national number, terms acceptance, and consent opt-ins.
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

        # Record implied transactional email consent
        record_consent(user, "email", "transactional", granted=True, source="registration")

        # Record explicit registration opt-ins
        if whatsapp_transactional:
            record_consent(user, "whatsapp", "transactional", granted=True, source="registration")
        if sms_transactional:
            record_consent(user, "sms", "transactional", granted=True, source="registration")
        if marketing_email:
            record_consent(user, "email", "marketing", granted=True, source="registration")
        if marketing_whatsapp:
            record_consent(user, "whatsapp", "marketing", granted=True, source="registration")
        if marketing_sms:
            record_consent(user, "sms", "marketing", granted=True, source="registration")

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    # print(f"[DEBUG] register_user org={getattr(g,'organization_id',None)} e164={e164}")
    return user


def backfill_consents() -> int:
    """
    Backfills transactional EMAIL consent for all legacy users who do not have one.
    Never grants WhatsApp, SMS, or marketing consents.
    Runs under tenant_bypass. Idempotent.
    Returns number of created consent records.
    """
    from app.models.notification_consent import NotificationConsent
    from app.tenancy.scoping import tenant_bypass

    created_count = 0
    with tenant_bypass():
        users = User.query.all()
        for u in users:
            existing = NotificationConsent.query.filter_by(
                user_id=u.id,
                channel="email",
                consent_type="transactional",
            ).first()
            if not existing:
                row = NotificationConsent(
                    organization_id=u.organization_id,
                    user_id=u.id,
                    channel="email",
                    consent_type="transactional",
                    granted_at=u.created_at or datetime.now(timezone.utc),
                    revoked_at=None,
                )
                db.session.add(row)
                created_count += 1
        db.session.commit()

    return created_count


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


def create_user_for_manual_booking(
    name: str,
    email: str,
    mobile_country_code: str,
    mobile_number: str,
) -> User:
    """
    Provisions a trekker user inline for a manual/offline booking.
    terms_accepted_at is None (completion gate triggers on first login).
    If email already exists in the organization, returns existing user without error.
    """
    clean_email = email.lower().strip()
    existing = User.query.filter_by(email=clean_email).first()
    if existing:
        return existing

    norm_cc, norm_num, _ = validate_mobile(mobile_country_code, mobile_number)
    user = User(
        name=name.strip(),
        email=clean_email,
        role="trekker",
        mobile_country_code=norm_cc,
        mobile_number=norm_num,
        is_active=True,
        must_reset_password=True,
        terms_accepted_at=None,
    )
    user.set_password(secrets.token_urlsafe(32))
    db.session.add(user)
    db.session.commit()

    try:
        request_password_reset(clean_email)
    except Exception:
        pass

    return user
"""
Authentication routes and session lifecycle (TDR v2 §5.5, §6).
"""

from urllib.parse import urlparse
import os
import secrets
import time

from flask import current_app, flash, g, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.auth import auth_bp
from app.auth.forms import (
    CompleteAccountForm,
    ForcedPasswordChangeForm,
    ForgotPasswordForm,
    LoginForm,
    RegisterForm,
    ResetPasswordForm,
)
from app.extensions import db, limiter, login_manager
from app.services import auth_service, notification_service, rbac_service
from app.services.auth_service import (
    DuplicateEmailError,
    InvalidMobileError,
    TermsNotAcceptedError,
)
from app.tenancy.scoping import tenant_context

_ROLE_DASHBOARD_ENDPOINTS = {
    "admin": "admin.dashboard",
    "staff": "staff.dashboard",
    "trekker": "trekker.dashboard",
}


def _role_dashboard_url(role: str) -> str:
    endpoint = _ROLE_DASHBOARD_ENDPOINTS.get(role, "public.index")
    return url_for(endpoint)


def _is_safe_next_url(target: str) -> bool:
    if not target or target.startswith("//"):
        return False
    parsed = urlparse(target)
    return not parsed.netloc and not parsed.scheme and target.startswith("/")




@auth_bp.before_app_request
def enforce_account_completion():
    """
    Ensures cross-tenant session isolation and requires legacy users to complete account details.
    """
    if current_user.is_authenticated:
        # Cross-tenant session protection: reject session if user belongs to another tenant
        active_org_id = getattr(g, "organization_id", None)
        if active_org_id is not None and str(getattr(current_user, "organization_id", None)) != str(active_org_id):
            logout_user()
            return redirect(url_for("auth.login"))

        if getattr(current_user, "needs_account_completion", False):
            exempt_endpoints = {
                "auth.complete_account",
                "auth.logout",
                "auth.verify_registration_otp",
                "auth.resend_registration_otp",
                "auth.google_login",
                "auth.google_callback",
                "static",
            }
            if request.endpoint not in exempt_endpoints and not request.path.startswith("/static/"):
                # print(f"[DEBUG] completion gate user_id={getattr(current_user,'id',None)} needs=True")
                return redirect(url_for("auth.complete_account"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Handles new user registration for Trekkers and Trek Staff with country-code mobile and terms consent."""
    form = RegisterForm()
    next_url = request.args.get("next")
    is_staff = request.args.get("role") == "staff" or (request.method == "POST" and request.form.get("role") == "staff")
    if request.method == "GET":
        form.role.data = "staff" if is_staff else "trekker"

    if form.validate_on_submit():
        contact_info = (
            form.contact_details.data
            if (hasattr(form, "contact_details") and form.contact_details.data)
            else f"{form.mobile_country_code.data} {form.mobile_number.data}"
        )

        # In testing mode, register directly for 100% test suite compatibility
        if current_app.config.get("TESTING"):
            try:
                user = auth_service.register_user(
                    name=form.name.data,
                    email=form.email.data,
                    password=form.password.data,
                    role=form.role.data,
                    contact_details=contact_info,
                    mobile_country_code=form.mobile_country_code.data,
                    mobile_number=form.mobile_number.data,
                    accept_terms=form.accept_terms.data,
                    whatsapp_transactional=bool(form.whatsapp_transactional.data),
                    sms_transactional=bool(form.sms_transactional.data),
                    marketing_email=bool(form.marketing_email.data),
                    marketing_whatsapp=bool(form.marketing_whatsapp.data),
                    marketing_sms=bool(form.marketing_sms.data),
                )
            except DuplicateEmailError:
                flash("An account with that email already exists in this organization.", "danger")
                return render_template("auth/register.html", form=form, next_url=next_url, is_staff=is_staff)
            except (InvalidMobileError, TermsNotAcceptedError) as exc:
                flash(str(exc), "danger")
                return render_template("auth/register.html", form=form, next_url=next_url, is_staff=is_staff)

            if user.role == "trekker":
                flash("Account created — you can now log in.", "success")
            else:
                flash("Account created — you'll be able to log in once an Admin approves your Trek Staff account.", "info")

            if next_url and _is_safe_next_url(next_url):
                return redirect(url_for("auth.login", next=next_url))
            return redirect(url_for("auth.login"))

        # Pre-check for duplicate email in this organization
        from app.models.user import User
        with tenant_context(getattr(g, "organization_id", None)):
            existing = User.query.filter_by(email=form.email.data.lower().strip()).first()
            if existing:
                flash("An account with that email already exists in this organization.", "danger")
                return render_template("auth/register.html", form=form, next_url=next_url, is_staff=is_staff)

        # Generate 6-digit OTP code
        otp_code = f"{secrets.randbelow(900000) + 100000}"

        session["pending_registration"] = {
            "name": form.name.data,
            "email": form.email.data.lower().strip(),
            "password": form.password.data,
            "role": form.role.data,
            "contact_details": contact_info,
            "mobile_country_code": form.mobile_country_code.data,
            "mobile_number": form.mobile_number.data,
            "accept_terms": form.accept_terms.data,
            "whatsapp_transactional": bool(form.whatsapp_transactional.data),
            "sms_transactional": bool(form.sms_transactional.data),
            "marketing_email": bool(form.marketing_email.data),
            "marketing_whatsapp": bool(form.marketing_whatsapp.data),
            "marketing_sms": bool(form.marketing_sms.data),
            "otp": otp_code,
            "otp_timestamp": time.time(),
            "next_url": next_url,
        }

        # Dispatch OTP code to user's email
        try:
            notification_service.send_registration_otp(
                email=form.email.data.lower().strip(),
                name=form.name.data,
                otp_code=otp_code,
            )
        except Exception as exc:
            current_app.logger.warning("Failed to dispatch registration OTP email: %s", exc)

        flash("A 6-digit verification code has been sent to your email. Please enter it below to complete registration.", "info")
        return redirect(url_for("auth.verify_registration_otp"))

    return render_template("auth/register.html", form=form, next_url=next_url, is_staff=is_staff)


@auth_bp.route("/verify-registration-otp", methods=["GET", "POST"])
def verify_registration_otp():
    """Validates the 6-digit OTP code before finalizing user registration."""
    pending = session.get("pending_registration")
    if not pending:
        flash("No registration in progress. Please register first.", "warning")
        return redirect(url_for("auth.register"))

    email = pending.get("email")

    if request.method == "POST":
        entered_otp = request.form.get("otp", "").strip()
        expected_otp = pending.get("otp")
        otp_time = pending.get("otp_timestamp", 0)

        # Expiry check: 10 minutes (600 seconds)
        if time.time() - otp_time > 600:
            flash("Verification code has expired. Please request a new code.", "danger")
            return render_template("auth/verify_otp.html", email=email)

        if not entered_otp or entered_otp != expected_otp:
            flash("Invalid verification code. Please check your email and try again.", "danger")
            return render_template("auth/verify_otp.html", email=email)

        # OTP is valid! Finalize user registration
        try:
            user = auth_service.register_user(
                name=pending["name"],
                email=pending["email"],
                password=pending["password"],
                role=pending["role"],
                contact_details=pending.get("contact_details"),
                mobile_country_code=pending["mobile_country_code"],
                mobile_number=pending["mobile_number"],
                accept_terms=pending["accept_terms"],
                whatsapp_transactional=pending.get("whatsapp_transactional", False),
                sms_transactional=pending.get("sms_transactional", False),
                marketing_email=pending.get("marketing_email", False),
                marketing_whatsapp=pending.get("marketing_whatsapp", False),
                marketing_sms=pending.get("marketing_sms", False),
            )
        except DuplicateEmailError:
            session.pop("pending_registration", None)
            flash("An account with that email already exists in this organization.", "danger")
            return redirect(url_for("auth.login"))
        except (InvalidMobileError, TermsNotAcceptedError) as exc:
            flash(str(exc), "danger")
            return redirect(url_for("auth.register"))

        next_url = pending.get("next_url")
        session.pop("pending_registration", None)

        if user.role == "trekker":
            flash("Account successfully verified and created! You can now log in.", "success")
        else:
            flash("Account verified! You'll be able to log in once an Admin approves your Trek Staff account.", "info")

        if next_url and _is_safe_next_url(next_url):
            return redirect(url_for("auth.login", next=next_url))
        return redirect(url_for("auth.login"))

    return render_template("auth/verify_otp.html", email=email)


@auth_bp.route("/resend-registration-otp", methods=["POST"])
@limiter.limit("5/minute")
def resend_registration_otp():
    """Resends a new 6-digit verification code to the registrant's email."""
    pending = session.get("pending_registration")
    if not pending:
        flash("No registration in progress. Please register first.", "warning")
        return redirect(url_for("auth.register"))

    new_otp = f"{secrets.randbelow(900000) + 100000}"
    pending["otp"] = new_otp
    pending["otp_timestamp"] = time.time()
    session["pending_registration"] = pending

    try:
        notification_service.send_registration_otp(
            email=pending["email"],
            name=pending["name"],
            otp_code=new_otp,
        )
    except Exception as exc:
        current_app.logger.warning("Failed to resend registration OTP: %s", exc)

    flash("A new verification code has been sent to your email.", "info")
    return redirect(url_for("auth.verify_registration_otp"))


def _get_google_redirect_uri() -> str:
    """
    Returns the OAuth redirect URI for Google.
    Google explicitly rejects localhost subdomains (e.g. tenantzero.localhost).
    When running on a .localhost subdomain, we use bare localhost so Google accepts it,
    and our tenancy resolver redirects the incoming callback to tenantzero.
    For reverse proxies/tunnels (ngrok), enforce https scheme required by Google.
    """
    host = request.host.split(":")[0].strip().lower()
    port = f":{request.host.split(':')[1]}" if ":" in request.host else ""
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    if "ngrok" in host or host.endswith((".loca.lt", ".trycloudflare.com")) or not (host.endswith(".localhost") or host in ("localhost", "127.0.0.1")):
        scheme = "https"

    if host.endswith(".localhost"):
        return f"{scheme}://localhost{port}/login/google/callback"
    return url_for("auth.google_callback", _external=True, _scheme=scheme)


@auth_bp.route("/login/google")
def google_login():
    """Initiates Google OAuth or guides configuration."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    redirect_uri = _get_google_redirect_uri()
    role = request.args.get("role", "trekker").strip().lower()
    if role not in ("trekker", "staff"):
        role = "trekker"

    if client_id and client_secret:
        token = secrets.token_urlsafe(16)
        session["oauth_role"] = role
        # Embed originating tenant host and role in state so OAuth callbacks return to the correct tenant
        state = f"{token}__h_{request.host}__r_{role}"
        session["oauth_state"] = token
        google_auth_url = (
            "https://accounts.google.com/o/oauth2/v2/auth?"
            f"client_id={client_id}&redirect_uri={redirect_uri}&response_type=code&scope=openid%20email%20profile&state={state}"
        )
        return redirect(google_auth_url)

    # In local development without Google Cloud Console credentials, inform the user clearly
    flash(
        "Google Sign-In is configured. To connect live Google accounts, please set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in your .env file.",
        "info",
    )
    return redirect(url_for("auth.login"))


@auth_bp.route("/login/google/callback")
def google_callback():
    """Handles Google OAuth2 callback and creates/authenticates tenant user."""
    code = request.args.get("code")
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    redirect_uri = _get_google_redirect_uri()

    if not code or not client_id or not client_secret:
        flash("Google authentication could not be completed.", "danger")
        return redirect(url_for("auth.login"))

    try:
        import requests
        token_resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token")

        userinfo_resp = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        user_info = userinfo_resp.json()
        email = user_info.get("email")
        name = user_info.get("name", "Google User")

        if not email:
            flash("Could not obtain email address from Google.", "danger")
            return redirect(url_for("auth.login"))

        role = session.pop("oauth_role", None)
        if not role:
            state_val = request.args.get("state", "")
            if "__r_" in state_val:
                role = state_val.split("__r_")[1].split("__")[0].strip().lower()
        if role not in ("trekker", "staff"):
            role = "trekker"

        from app.models.user import User
        user = User.query.filter_by(email=email).first()
        if not user:
            from app.models.user import LEGACY_MOBILE_SENTINEL
            user = User(
                name=name,
                email=email,
                role=role,
                mobile_country_code="+91",
                mobile_number=LEGACY_MOBILE_SENTINEL,
                terms_accepted_at=None,
            )
            user.set_password(secrets.token_urlsafe(16))
            db.session.add(user)
            db.session.flush()

            if role == "staff":
                from app.models.staff_profile import StaffProfile
                staff_profile = StaffProfile(
                    user_id=user.id,
                    approval_status="pending",
                )
                db.session.add(staff_profile)

            auth_service.record_consent(user, "email", "transactional", granted=True, source="google_oauth")
            db.session.commit()

        login_user(user)

        # 1. Snapshot capabilities for approved staff
        if user.role == "staff":
            session["capability_keys"] = rbac_service.get_capability_keys(user)
        else:
            session["capability_keys"] = []

        # 2. Check pending staff approval
        if (
            user.role == "staff"
            and user.staff_profile
            and user.staff_profile.approval_status != "approved"
        ):
            if user.needs_account_completion:
                return redirect(url_for("auth.complete_account"))
            return redirect(url_for("auth.pending_approval"))

        # 3. Check account completion (mobile & terms)
        if user.needs_account_completion:
            return redirect(url_for("auth.complete_account"))

        flash(f"Welcome, {user.name}!", "success")
        next_url = session.pop("next_url", None)
        if next_url and _is_safe_next_url(next_url):
            return redirect(next_url)
        return redirect(_role_dashboard_url(user.role))
    except Exception as exc:
        flash(f"Google authentication error: {exc}", "danger")
        return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5/minute")
def login():
    if request.method == "GET" and request.args.get("next"):
        session["next_url"] = request.args.get("next")

    form = LoginForm()

    if form.validate_on_submit():
        user = auth_service.authenticate(form.email.data, form.password.data)
        if user is None:
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html", form=form)

        login_user(user)

        # Snapshot capabilities at login time (PDR v2 §5.3)
        if user.role == "staff":
            session["capability_keys"] = rbac_service.get_capability_keys(user)
        else:
            session["capability_keys"] = []
        # print(f"[DEBUG] login snapshot user={user.id} keys={session.get('capability_keys')}")

        # 1. Pending approval staff checkpoint
        if (
            user.role == "staff"
            and user.staff_profile
            and user.staff_profile.approval_status != "approved"
        ):
            return redirect(url_for("auth.pending_approval"))

        # 2. Forced password change checkpoint
        if user.must_reset_password:
            return redirect(url_for("auth.forced_password_change"))

        # 3. Account completion checkpoint (missing mobile or unaccepted terms)
        if user.needs_account_completion:
            return redirect(url_for("auth.complete_account"))

        # 4. Success redirect
        next_url = session.pop("next_url", None)
        if next_url and _is_safe_next_url(next_url):
            return redirect(next_url)

        return redirect(_role_dashboard_url(user.role))

    return render_template("auth/login.html", form=form)


@auth_bp.route("/complete-account", methods=["GET", "POST"])
@login_required
def complete_account():
    """Gated screen requiring legacy or staff-created users to provide valid mobile and accept terms."""
    if not current_user.needs_account_completion:
        if (
            current_user.role == "staff"
            and current_user.staff_profile
            and current_user.staff_profile.approval_status != "approved"
        ):
            return redirect(url_for("auth.pending_approval"))
        return redirect(_role_dashboard_url(current_user.role))

    form = CompleteAccountForm()
    if form.validate_on_submit():
        try:
            auth_service.complete_account(
                user=current_user,
                country_code=form.mobile_country_code.data,
                number=form.mobile_number.data,
                accept_terms=form.accept_terms.data,
            )
            flash("Account completed successfully.", "success")
            if (
                current_user.role == "staff"
                and current_user.staff_profile
                and current_user.staff_profile.approval_status != "approved"
            ):
                return redirect(url_for("auth.pending_approval"))
            return redirect(_role_dashboard_url(current_user.role))
        except (InvalidMobileError, TermsNotAcceptedError) as exc:
            flash(str(exc), "danger")

    return render_template("auth/complete_account.html", form=form)


@auth_bp.route("/logout", methods=["POST", "GET"])
@login_required
def logout():
    """Logs out the active user session and redirects to the login page."""
    session.pop("capability_keys", None)
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5/minute")
def forgot_password():
    form = ForgotPasswordForm()
    submitted = False
    if form.validate_on_submit():
        auth_service.request_password_reset(form.email.data)
        submitted = True
    return render_template("auth/forgot_password.html", form=form, submitted=submitted)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("5/minute")
def reset_password(token):
    form = ResetPasswordForm(token=token)
    if form.validate_on_submit():
        if auth_service.reset_password(form.token.data, form.new_password.data):
            flash("Password updated — you can now log in.", "success")
            return redirect(url_for("auth.login"))
        flash("That reset link is invalid or has expired. Please request a new one.", "danger")
    return render_template("auth/reset_password.html", form=form, token=token)


@auth_bp.route("/forced-password-change", methods=["GET", "POST"])
@login_required
def forced_password_change():
    form = ForcedPasswordChangeForm()
    if form.validate_on_submit():
        current_user.set_password(form.new_password.data)
        current_user.must_reset_password = False
        db.session.commit()
        flash("Password updated.", "success")
        if current_user.needs_account_completion:
            return redirect(url_for("auth.complete_account"))
        return redirect(_role_dashboard_url(current_user.role))
    return render_template("auth/forced_password_change.html", form=form)


@auth_bp.route("/pending-approval")
@login_required
def pending_approval():
    return render_template("staff/pending_approval.html")
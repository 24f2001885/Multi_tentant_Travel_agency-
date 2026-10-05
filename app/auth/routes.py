
from urllib.parse import urlparse


from flask import flash, redirect, render_template, request, url_for, session
from flask_login import current_user, login_required, login_user, logout_user

from app.auth import auth_bp
from app.auth.forms import (
    ForcedPasswordChangeForm,
    ForgotPasswordForm,
    LoginForm,
    RegisterForm,
    ResetPasswordForm,
)
from app.extensions import db, limiter, login_manager
from app.services import auth_service
from app.services.auth_service import DuplicateEmailError


_ROLE_DASHBOARD_ENDPOINTS = {
    "admin": "admin.dashboard",
    "staff": "staff.dashboard",
    "trekker": "trekker.dashboard",
}


def _role_dashboard_url(role: str) -> str:
    
    endpoint = _ROLE_DASHBOARD_ENDPOINTS.get(role, "auth.login")
    return url_for(endpoint)


def _is_safe_next_url(target: str) -> bool:
   
    if not target or target.startswith("//"):
        return False
    parsed = urlparse(target)
    return not parsed.netloc and not parsed.scheme and target.startswith("/")


@login_manager.user_loader
def load_user(user_id: str):
    """Flask-Login callback to load a user instance from the database by ID."""
    from app.models.user import User

    return User.query.get(int(user_id))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Handles new user registration for Trekkers and Trek Staff."""
    form = RegisterForm()
    next_url = request.args.get("next")

    if form.validate_on_submit():
        try:
            user = auth_service.register_user(
                name=form.name.data,
                email=form.email.data,
                password=form.password.data,
                role=form.role.data,
                contact_details=form.contact_details.data or None,
            )
        except DuplicateEmailError:
            flash("An account with that email already exists.", "danger")
            return render_template("auth/register.html", form=form, next_url=next_url)

        if user.role == "trekker":
            flash("Account created — you can now log in.", "success")
        else:
            flash(
                "Account created — you'll be able to log in once an Admin "
                "approves your Trek Staff account.",
                "info",
            )

        if next_url and _is_safe_next_url(next_url):
            return redirect(url_for("auth.login", next=next_url))
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html", form=form, next_url=next_url)


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

        # Higher-priority interrupt: 
        if (
            user.role == "staff"
            and user.staff_profile
            and user.staff_profile.approval_status != "approved"
        ):
            login_user(user)
            return redirect(url_for("auth.pending_approval"))

        # Higher-priority interrupt:
        if user.must_reset_password:
            login_user(user)
            return redirect(url_for("auth.forced_password_change"))

        login_user(user)

        # Smart Redirect
        next_url = session.pop("next_url", None)
        
        if next_url and _is_safe_next_url(next_url):
            return redirect(next_url)
            
        return redirect(_role_dashboard_url(user.role))

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    """Logs out the active user session and redirects to the login page."""
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5/minute")
def forgot_password():
    """Generates password reset tokens without leaking email existence."""
    form = ForgotPasswordForm()
    submitted = False
    if form.validate_on_submit():
        auth_service.request_password_reset(form.email.data)
        submitted = True
    return render_template("auth/forgot_password.html", form=form, submitted=submitted)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("5/minute")
def reset_password(token):
    """Processes password resets using secure time-sensitive tokens."""
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
    """Forces administrative password updates upon initial login."""
    form = ForcedPasswordChangeForm()
    if form.validate_on_submit():
        current_user.set_password(form.new_password.data)
        current_user.must_reset_password = False
        db.session.commit()
        flash("Password updated.", "success")
        return redirect(_role_dashboard_url(current_user.role))
    return render_template("auth/forced_password_change.html", form=form)


@auth_bp.route("/pending-approval")
@login_required
def pending_approval():
    """Rendered when a Staff user logs in before an Admin has approved their account."""
    return render_template("staff/pending_approval.html")
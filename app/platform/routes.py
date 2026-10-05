"""
Platform Admin console routes (UI/UX v2 §3.2).
Every route except login uses @platform_admin_required. Runs under tenant_bypass().
"""

import os
import uuid
from typing import Any

from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func

from app.extensions import db, limiter
from app.models.booking import Booking
from app.models.domain import Domain
from app.models.organization import Organization
from app.platform import platform_bp
from app.platform.auth import (
    current_platform_admin,
    platform_admin_required,
    platform_login,
    platform_logout,
)
from app.platform.forms import (
    CustomDomainForm,
    OnboardOrganizationForm,
    PlatformChangePasswordForm,
    PlatformLoginForm,
    PlatformProfileForm,
)
from app.services import tenancy_service
from app.tenancy.scoping import tenant_bypass


@platform_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login() -> Any:
    """Platform Admin login route with rate limiting."""
    if current_platform_admin():
        return redirect(url_for("platform.dashboard"))

    form = PlatformLoginForm()
    if form.validate_on_submit():
        admin = platform_login(form.email.data, form.password.data)
        if admin:
            flash(f"Welcome back, {admin.name}.", "success")
            return redirect(url_for("platform.dashboard"))
        flash("Invalid email or password.", "danger")

    # print(f"[DEBUG] platform route {request.path} admin={session.get('platform_admin_id')}")
    return render_template("platform/login.html", form=form)


@platform_bp.route("/logout", methods=["POST"])
def logout() -> Any:
    """Platform Admin logout."""
    platform_logout()
    flash("You have been signed out of the Platform Console.", "info")
    return redirect(url_for("platform.login"))


@platform_bp.route("/dashboard", methods=["GET"])
@platform_admin_required
def dashboard() -> Any:
    """Platform dashboard showing tenant statistics and status overviews."""
    admin = current_platform_admin()

    with tenant_bypass():
        total_orgs = Organization.query.count()
        active_orgs = Organization.query.filter_by(status="active").count()
        suspended_orgs = Organization.query.filter_by(status="suspended").count()
        total_bookings = Booking.query.count()

    orgs_data = tenancy_service.list_organizations_with_counts()

    # print(f"[DEBUG] platform route {request.path} admin={session.get('platform_admin_id')}")
    return render_template(
        "platform/dashboard.html",
        admin=admin,
        total_orgs=total_orgs,
        active_orgs=active_orgs,
        suspended_orgs=suspended_orgs,
        total_bookings=total_bookings,
        orgs=orgs_data,
    )


@platform_bp.route("/organizations", methods=["GET"])
@platform_admin_required
def organizations_list() -> Any:
    """List of all tenant organizations."""
    admin = current_platform_admin()
    orgs_data = tenancy_service.list_organizations_with_counts()
    # print(f"[DEBUG] platform route {request.path} admin={session.get('platform_admin_id')}")
    return render_template("platform/organizations_list.html", admin=admin, orgs=orgs_data)


@platform_bp.route("/organizations/new", methods=["GET", "POST"])
@platform_admin_required
def organization_new() -> Any:
    """Onboards a new organization and shows the one-time temporary password."""
    admin = current_platform_admin()
    form = OnboardOrganizationForm()
    base_domain = os.environ.get("BASE_DOMAIN", "localhost")

    if form.validate_on_submit():
        try:
            org, admin_user, temp_password = tenancy_service.create_organization(
                name=form.name.data,
                subdomain=form.subdomain.data,
                tenant_admin_email=form.tenant_admin_email.data,
                tenant_admin_name=form.tenant_admin_name.data,
                platform_admin=admin,
            )
            # Render one-time temporary password result state
            return render_template(
                "platform/onboard_organization.html",
                admin=admin,
                form=form,
                temp_password=temp_password,
                new_org=org,
                admin_user=admin_user,
                base_domain=base_domain,
            )
        except ValueError as err:
            flash(str(err), "danger")

    # print(f"[DEBUG] platform route {request.path} admin={session.get('platform_admin_id')}")
    return render_template(
        "platform/onboard_organization.html",
        admin=admin,
        form=form,
        temp_password=None,
        base_domain=base_domain,
    )


@platform_bp.route("/organizations/<uuid:id>", methods=["GET"])
@platform_admin_required
def organization_detail(id: uuid.UUID) -> Any:
    """Detailed view of an organization, domains, and controls."""
    admin = current_platform_admin()
    with tenant_bypass():
        org = Organization.query.get(id)
        if not org:
            abort(404)
        domains = Domain.query.filter_by(organization_id=org.id).order_by(Domain.is_primary.desc()).all()

    domain_form = CustomDomainForm()
    # print(f"[DEBUG] platform route {request.path} admin={session.get('platform_admin_id')}")
    return render_template(
        "platform/organization_detail.html",
        admin=admin,
        org=org,
        domains=domains,
        domain_form=domain_form,
    )


@platform_bp.route("/organizations/<uuid:id>/suspend", methods=["POST"])
@platform_admin_required
def organization_suspend(id: uuid.UUID) -> Any:
    """Suspends an organization."""
    admin = current_platform_admin()
    with tenant_bypass():
        org = Organization.query.get(id)
        if not org:
            abort(404)
    tenancy_service.suspend_organization(org, admin)
    flash(f"Organization '{org.name}' has been suspended.", "warning")
    return redirect(url_for("platform.organization_detail", id=id))


@platform_bp.route("/organizations/<uuid:id>/reactivate", methods=["POST"])
@platform_admin_required
def organization_reactivate(id: uuid.UUID) -> Any:
    """Reactivates a suspended organization."""
    admin = current_platform_admin()
    with tenant_bypass():
        org = Organization.query.get(id)
        if not org:
            abort(404)
    tenancy_service.reactivate_organization(org, admin)
    flash(f"Organization '{org.name}' has been reactivated.", "success")
    return redirect(url_for("platform.organization_detail", id=id))


@platform_bp.route("/organizations/<uuid:id>/domains", methods=["POST"])
@platform_admin_required
def organization_add_domain(id: uuid.UUID) -> Any:
    """Adds a custom domain to an organization."""
    with tenant_bypass():
        org = Organization.query.get(id)
        if not org:
            abort(404)

    form = CustomDomainForm()
    if form.validate_on_submit():
        try:
            tenancy_service.add_custom_domain(org, form.domain_name.data)
            flash(f"Custom domain '{form.domain_name.data}' added.", "success")
        except ValueError as err:
            flash(str(err), "danger")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(error, "danger")

    return redirect(url_for("platform.organization_detail", id=id))


@platform_bp.route("/domains/<uuid:id>/verify", methods=["POST"])
@platform_admin_required
def domain_verify(id: uuid.UUID) -> Any:
    """Marks a custom domain as verified."""
    admin = current_platform_admin()
    with tenant_bypass():
        domain = Domain.query.get(id)
        if not domain:
            abort(404)
    tenancy_service.mark_domain_verified(domain, admin)
    flash(f"Domain '{domain.domain_name}' marked as verified.", "success")
    return redirect(url_for("platform.organization_detail", id=domain.organization_id))


@platform_bp.route("/organizations/<uuid:id>/audit-logs", methods=["GET"])
@platform_admin_required
def organization_audit_logs(id: uuid.UUID) -> Any:
    """Cross-tenant audit log viewer with recorded meta-audit access."""
    admin = current_platform_admin()
    with tenant_bypass():
        org = Organization.query.get(id)
        if not org:
            abort(404)

    page = request.args.get("page", 1, type=int)
    audit_pagination = tenancy_service.read_tenant_audit_log(
        platform_admin=admin,
        org=org,
        page=page,
        per_page=20,
    )

    # print(f"[DEBUG] platform route {request.path} admin={session.get('platform_admin_id')}")
    return render_template(
        "platform/audit_viewer.html",
        admin=admin,
        org=org,
        pagination=audit_pagination,
        page=page,
    )


@platform_bp.route("/profile", methods=["GET", "POST"])
@platform_admin_required
def profile() -> Any:
    """Platform Admin profile settings and password change."""
    admin = current_platform_admin()
    profile_form = PlatformProfileForm(name=admin.name)
    password_form = PlatformChangePasswordForm()

    if request.method == "POST":
        action = request.form.get("form_action")
        if action == "profile" and profile_form.validate_on_submit():
            with tenant_bypass():
                target = PlatformAdmin.query.get(admin.id)
                target.name = profile_form.name.data.strip()
                db.session.commit()
            flash("Profile updated successfully.", "success")
            return redirect(url_for("platform.profile"))

        elif action == "password" and password_form.validate_on_submit():
            with tenant_bypass():
                target = PlatformAdmin.query.get(admin.id)
                if not target.check_password(password_form.current_password.data):
                    flash("Incorrect current password.", "danger")
                else:
                    target.set_password(password_form.new_password.data)
                    db.session.commit()
                    flash("Password changed successfully.", "success")
                    return redirect(url_for("platform.profile"))

    # print(f"[DEBUG] platform route {request.path} admin={session.get('platform_admin_id')}")
    return render_template(
        "platform/profile.html",
        admin=admin,
        profile_form=profile_form,
        password_form=password_form,
    )

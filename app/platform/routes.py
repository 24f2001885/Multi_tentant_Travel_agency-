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
from app.models.platform_admin import PlatformAdmin
from app.platform import platform_bp, platform_root_bp
from app.platform.auth import (
    current_platform_admin,
    platform_admin_required,
    platform_login,
    platform_logout,
)
from app.platform.forms import (
    CustomDomainForm,
    OnboardOrganizationForm,
    OrganizationPayoutForm,
    PlatformChangePasswordForm,
    PlatformLoginForm,
    PlatformProfileForm,
)
from app.services import tenancy_service
from app.services.tenant_features_service import (
    AVAILABLE_TENANT_FEATURES,
    SUBSCRIPTION_TIERS,
    update_organization_subscription,
)
from app.tenancy.scoping import tenant_bypass


@platform_bp.route("/", methods=["GET"])
@platform_bp.route("", methods=["GET"])
def platform_root() -> Any:
    """Redirects platform root to platform dashboard / login."""
    if current_platform_admin():
        return redirect(url_for("platform.dashboard"))
    return redirect(url_for("platform.login"))


@platform_root_bp.route("/", methods=["GET"])
def platform_root_index() -> Any:
    """Root redirect on PLATFORM_ADMIN_HOST to platform dashboard / login."""
    if current_platform_admin():
        return redirect(url_for("platform.dashboard"))
    return redirect(url_for("platform.login"))


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
    from app.services import monitoring_service
    business_metrics = monitoring_service.get_platform_business_metrics()
    infra_metrics = monitoring_service.get_platform_infra_metrics()

    # print(f"[DEBUG] platform route {request.path} admin={session.get('platform_admin_id')}")
    return render_template(
        "platform/dashboard.html",
        admin=admin,
        total_orgs=total_orgs,
        active_orgs=active_orgs,
        suspended_orgs=suspended_orgs,
        total_bookings=total_bookings,
        orgs=orgs_data,
        business=business_metrics,
        infra=infra_metrics,
    )


@platform_bp.route("/organizations", methods=["GET"])
@platform_root_bp.route("/organizations", methods=["GET"])
@platform_admin_required
def organizations_list() -> Any:
    """List of all tenant organizations."""
    admin = current_platform_admin()
    orgs_data = tenancy_service.list_organizations_with_counts()
    # print(f"[DEBUG] platform route {request.path} admin={session.get('platform_admin_id')}")
    return render_template("platform/organizations_list.html", admin=admin, orgs=orgs_data)


@platform_bp.route("/organizations/new", methods=["GET", "POST"])
@platform_root_bp.route("/organizations/new", methods=["GET", "POST"])
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
                theme=form.theme.data if hasattr(form, "theme") and form.theme.data else None,
                subscription_tier=form.subscription_tier.data if hasattr(form, "subscription_tier") and form.subscription_tier.data else "enterprise",
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
@platform_root_bp.route("/organizations/<uuid:id>", methods=["GET"])
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
    payout_form = OrganizationPayoutForm(
        razorpay_route_account_id=org.razorpay_route_account_id or "",
        gst_number=org.gst_number or "",
        theme=org.theme or "",
    )
    # print(f"[DEBUG] platform route {request.path} admin={session.get('platform_admin_id')}")
    return render_template(
        "platform/organization_detail.html",
        admin=admin,
        org=org,
        domains=domains,
        domain_form=domain_form,
        payout_form=payout_form,
        subscription_tiers=SUBSCRIPTION_TIERS,
        available_features=AVAILABLE_TENANT_FEATURES,
    )


@platform_bp.route("/organizations/<uuid:id>/subscription", methods=["POST"])
@platform_admin_required
def organization_update_subscription(id: uuid.UUID) -> Any:
    """Updates SaaS subscription tier and enabled features for a tenant."""
    admin = current_platform_admin()
    with tenant_bypass():
        org = Organization.query.get(id)
        if not org:
            abort(404)

        tier = request.form.get("subscription_tier", "starter").strip().lower()
        selected_features = request.form.getlist("features")

        update_organization_subscription(
            org=org,
            tier=tier,
            selected_features=selected_features,
            actor=admin,
        )

    flash(f"Subscription and feature entitlements updated for '{org.name}' ({tier.capitalize()}).", "success")
    return redirect(url_for("platform.organization_detail", id=id))


@platform_bp.route("/organizations/<uuid:id>/payout", methods=["POST"])
@platform_admin_required
def organization_update_payout(id: uuid.UUID) -> Any:
    """Updates Razorpay Route linked account, GST, and UI theme details."""
    with tenant_bypass():
        org = Organization.query.get(id)
        if not org:
            abort(404)

        route_acc = request.form.get("razorpay_route_account_id", "").strip()
        gst = request.form.get("gst_number", "").strip()
        theme = request.form.get("theme", "").strip()

        org.razorpay_route_account_id = route_acc if route_acc else None
        org.gst_number = gst if gst else None
        org.theme = theme if theme else None
        db.session.commit()

    flash(f"Payout and UI theme configuration updated for '{org.name}'.", "success")
    return redirect(url_for("platform.organization_detail", id=id))


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


# --- Milestone P13: Monitoring & Platform Backups ---
@platform_bp.route("/monitoring", methods=["GET"])
@platform_root_bp.route("/monitoring", methods=["GET"])
@platform_admin_required
def monitoring() -> Any:
    """Platform business and infrastructure load monitoring dashboard."""
    from app.services import monitoring_service
    admin = current_platform_admin()
    business_metrics = monitoring_service.get_platform_business_metrics()
    infra_metrics = monitoring_service.get_platform_infra_metrics()
    return render_template(
        "platform/monitoring.html",
        admin=admin,
        business=business_metrics,
        infra=infra_metrics,
    )


@platform_bp.route("/backups", methods=["GET"])
@platform_root_bp.route("/backups", methods=["GET"])
@platform_admin_required
def backups() -> Any:
    """Platform & tenant backup console."""
    from app.models.tenant_backup import TenantBackup
    admin = current_platform_admin()
    with tenant_bypass():
        all_backups = TenantBackup.query.order_by(TenantBackup.created_at.desc()).all()
        orgs = Organization.query.order_by(Organization.name.asc()).all()
    return render_template(
        "platform/backups.html",
        admin=admin,
        backups=all_backups,
        orgs=orgs,
    )


@platform_bp.route("/backups/trigger", methods=["POST"])
@platform_root_bp.route("/backups/trigger", methods=["POST"])
@platform_admin_required
def trigger_backup() -> Any:
    """Triggers either full platform backup or a targeted tenant export."""
    from app.services import backup_service
    admin = current_platform_admin()
    target = request.form.get("target", "all").strip()

    try:
        if target == "all":
            backup = backup_service.create_platform_backup(platform_admin_id=admin.id)
            flash("Full platform backup created successfully.", "success")
        else:
            org_id = uuid.UUID(target)
            backup = backup_service.create_tenant_backup(
                organization_id=org_id,
                triggered_by_platform_admin_id=admin.id,
            )
            flash("Targeted tenant backup created successfully.", "success")
    except Exception as e:
        flash(f"Backup generation failed: {str(e)}", "danger")

    return redirect(url_for("platform.backups"))


@platform_bp.route("/backups/<uuid:id>/download", methods=["GET"])
@platform_root_bp.route("/backups/<uuid:id>/download", methods=["GET"])
@platform_admin_required
def download_backup(id: uuid.UUID) -> Any:
    """Allows platform admin to securely download any backup archive."""
    from flask import send_file
    from app.models.tenant_backup import TenantBackup
    with tenant_bypass():
        backup = TenantBackup.query.get_or_404(id)

    if not os.path.exists(backup.storage_location):
        abort(404)

    dl_name = f"platform_{backup.backup_type}_{backup.created_at.strftime('%Y%m%d_%H%M%S')}.json.gz"
    return send_file(backup.storage_location, as_attachment=True, download_name=dl_name, mimetype="application/gzip")


@platform_bp.route("/audit-logs", methods=["GET"])
@platform_root_bp.route("/audit-logs", methods=["GET"])
@platform_admin_required
def audit_logs() -> Any:
    """Meta-audit trail displaying platform admin accesses across tenants."""
    from app.models.platform_access_log import PlatformAccessLog
    admin = current_platform_admin()
    with tenant_bypass():
        logs = PlatformAccessLog.query.order_by(PlatformAccessLog.created_at.desc()).limit(100).all()
    return render_template("platform/audit_logs.html", admin=admin, logs=logs)


"""
Tenant Admin refund-tier configuration routes (UI/UX v2 §3.3; PDR v2 §14.1; Milestone P9).
"""

from decimal import Decimal
from flask import flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.admin import admin_bp
from app.auth.decorators import role_required, tenant_feature_required
from app.extensions import db
from app.models.refund_policy import RefundPolicy
from app.services import refund_service


@admin_bp.route("/settings/refund-policy", methods=["GET", "POST"])
@login_required
@role_required("admin")
@tenant_feature_required("refund_policy")
def refund_policy_settings():
    org = getattr(g, "organization", None)
    if not org:
        flash("Tenant context missing.", "danger")
        return redirect(url_for("admin.dashboard"))

    # Ensure defaults are seeded if tenant has no policy tiers yet
    refund_service.seed_default_policy(org)
    db.session.commit()

    if request.method == "POST":
        min_days_list = request.form.getlist("min_days_before_trip")
        max_days_list = request.form.getlist("max_days_before_trip")
        pct_list = request.form.getlist("refund_percentage")

        rows = []
        for i in range(len(min_days_list)):
            min_val = min_days_list[i].strip() if i < len(min_days_list) else ""
            max_val = max_days_list[i].strip() if i < len(max_days_list) else ""
            pct_val = pct_list[i].strip() if i < len(pct_list) else ""

            if not min_val and not pct_val:
                continue

            rows.append({
                "min_days_before_trip": min_val,
                "max_days_before_trip": max_val if max_val else None,
                "refund_percentage": pct_val,
            })

        try:
            refund_service.replace_policy(org, rows, actor=current_user)
            db.session.commit()
            flash("Refund policy updated successfully.", "success")
            return redirect(url_for("admin.refund_policy_settings"))
        except refund_service.PolicyValidationError as err:
            db.session.rollback()
            flash(f"Policy validation failed: {str(err)}", "danger")

    policies = RefundPolicy.query.filter_by(organization_id=org.id).order_by(
        RefundPolicy.min_days_before_trip.desc()
    ).all()
    return render_template("admin/refund_policy.html", policies=policies)

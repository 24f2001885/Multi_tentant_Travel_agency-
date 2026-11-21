"""
Protected manual-booking controls (UI/UX v2 §3.3, §4.5; PDR v2 §5.8, §11; Milestone P9).
Gated strictly to tenant owner via @protected_capability_required.
"""

import json
from flask import abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user

from app.admin import admin_bp
from app.auth.decorators import protected_capability_required, tenant_feature_required
from app.extensions import db
from app.models.booking import Booking
from app.models.audit_log import AuditLog
from app.services import payment_service


@admin_bp.route("/settings/manual-booking", methods=["GET", "POST"])
@protected_capability_required
@tenant_feature_required("manual_bookings")
def manual_booking_settings():
    org = g.organization
    if request.method == "POST":
        org.manual_booking_enabled = not org.manual_booking_enabled
        log = AuditLog(
            organization_id=org.id,
            user_id=current_user.id,
            action="manual_booking.toggle",
            target_type="organization",
            target_id=0,
            details=json.dumps({"enabled": org.manual_booking_enabled}),
        )
        db.session.add(log)
        db.session.commit()
        flash(
            f"Manual bookings are now {'enabled' if org.manual_booking_enabled else 'disabled'}.",
            "info",
        )
        return redirect(url_for("admin.manual_booking_settings"))

    return render_template("admin/manual_booking_settings.html", org=org)


@admin_bp.route("/manual-bookings", methods=["GET"])
@protected_capability_required
@tenant_feature_required("manual_bookings")
def manual_bookings_queue():
    org = g.organization
    pending_bookings = Booking.query.filter_by(
        organization_id=org.id,
        booking_channel="manual",
        status="pending",
    ).order_by(Booking.booking_date.asc()).all()

    return render_template("admin/manual_booking_queue.html", bookings=pending_bookings)


@admin_bp.route("/bookings/<hashid:id>/manual-sign-off", methods=["POST"])
@protected_capability_required
@tenant_feature_required("manual_bookings")
def manual_sign_off(id):
    org = g.organization
    booking = Booking.query.filter_by(id=id, organization_id=org.id).first_or_404()

    try:
        payment_service.confirm_manual_booking(booking, signer=current_user)
        flash(f"Booking #{booking.id} signed off and confirmed.", "success")
    except payment_service.SlotUnavailableError as err:
        flash(f"Cannot sign off: {str(err)}", "danger")

    return redirect(url_for("admin.manual_bookings_queue"))


@admin_bp.route("/bookings/<hashid:id>/manual-reject", methods=["POST"])
@protected_capability_required
@tenant_feature_required("manual_bookings")
def manual_reject(id):
    org = g.organization
    booking = Booking.query.filter_by(id=id, organization_id=org.id).first_or_404()
    reason = request.form.get("reason", "Manual booking rejected by administrator")

    payment_service.cancel_booking(booking, current_user, "staff_force_cancel", note=reason)
    flash(f"Manual booking #{booking.id} rejected and cancelled.", "warning")
    return redirect(url_for("admin.manual_bookings_queue"))

"""
Tenant Admin coupon management routes (UI/UX v2 §3.3; PDR v2 §14.1; Milestone P9).
"""

from datetime import datetime
import uuid
from flask import abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.admin import admin_bp
from app.auth.decorators import role_required, tenant_feature_required
from app.extensions import db
from app.models.coupon import Coupon
from app.services import coupon_service


@admin_bp.route("/coupons", methods=["GET"])
@login_required
@role_required("admin")
@tenant_feature_required("coupons")
def coupons_list():
    org = getattr(g, "organization", None)
    coupons = Coupon.query.filter_by(organization_id=org.id).order_by(Coupon.code.asc()).all()
    return render_template("admin/coupons_list.html", coupons=coupons)


@admin_bp.route("/coupons/new", methods=["GET", "POST"])
@login_required
@role_required("admin")
@tenant_feature_required("coupons")
def coupon_new():
    org = getattr(g, "organization", None)
    if request.method == "POST":
        valid_from = request.form.get("valid_from")
        valid_until = request.form.get("valid_until")

        data = {
            "code": request.form.get("code"),
            "discount_type": request.form.get("discount_type"),
            "discount_value": request.form.get("discount_value"),
            "valid_from": datetime.strptime(valid_from, "%Y-%m-%d").date() if valid_from else None,
            "valid_until": datetime.strptime(valid_until, "%Y-%m-%d").date() if valid_until else None,
            "max_uses": request.form.get("max_uses"),
            "min_booking_amount": request.form.get("min_booking_amount"),
            "is_active": "is_active" in request.form,
        }

        try:
            coupon = coupon_service.create_coupon(org, data, actor=current_user)
            db.session.commit()
            flash(f"Coupon '{coupon.code}' created successfully.", "success")
            return redirect(url_for("admin.coupons_list"))
        except coupon_service.CouponValidationError as err:
            db.session.rollback()
            flash(str(err), "danger")

    return render_template("admin/coupon_form.html", coupon=None)


@admin_bp.route("/coupons/<uuid:id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
@tenant_feature_required("coupons")
def coupon_edit(id):
    org = getattr(g, "organization", None)
    coupon = Coupon.query.filter_by(id=id, organization_id=org.id).first_or_404()

    if request.method == "POST":
        valid_from = request.form.get("valid_from")
        valid_until = request.form.get("valid_until")

        data = {
            "code": request.form.get("code"),
            "discount_type": request.form.get("discount_type"),
            "discount_value": request.form.get("discount_value"),
            "valid_from": datetime.strptime(valid_from, "%Y-%m-%d").date() if valid_from else None,
            "valid_until": datetime.strptime(valid_until, "%Y-%m-%d").date() if valid_until else None,
            "max_uses": request.form.get("max_uses"),
            "min_booking_amount": request.form.get("min_booking_amount"),
            "is_active": "is_active" in request.form,
        }

        try:
            coupon_service.update_coupon(coupon, data, actor=current_user)
            db.session.commit()
            flash(f"Coupon '{coupon.code}' updated successfully.", "success")
            return redirect(url_for("admin.coupons_list"))
        except coupon_service.CouponValidationError as err:
            db.session.rollback()
            flash(str(err), "danger")

    return render_template("admin/coupon_form.html", coupon=coupon)


@admin_bp.route("/coupons/<uuid:id>/toggle-active", methods=["POST"])
@login_required
@role_required("admin")
@tenant_feature_required("coupons")
def coupon_toggle_active(id):
    org = getattr(g, "organization", None)
    coupon = Coupon.query.filter_by(id=id, organization_id=org.id).first_or_404()
    coupon_service.toggle_coupon_active(coupon, actor=current_user)
    db.session.commit()
    flash(f"Coupon '{coupon.code}' is now {'active' if coupon.is_active else 'inactive'}.", "info")
    return redirect(url_for("admin.coupons_list"))

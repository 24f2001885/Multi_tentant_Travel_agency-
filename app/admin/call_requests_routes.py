"""
Tenant Admin queue for footer call requests (UI/UX v2 §3.3).
Attached to admin_bp.
"""

from uuid import UUID
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.admin import admin_bp
from app.auth.decorators import role_required
from app.extensions import db
from app.models.call_request import CallRequest
from app.services import call_request_service
from app.services.call_request_service import InvalidCallRequestTransition


@admin_bp.route("/call-requests", methods=["GET"])
@login_required
@role_required("admin")
def call_requests_list():
    """Lists and filters customer call-back requests for the active tenant."""
    status_filter = request.args.get("status")
    if status_filter not in ("new", "contacted", "closed"):
        status_filter = None

    items = call_request_service.list_requests(status=status_filter)
    # print(f"[DEBUG] call_requests list status={status_filter} count={len(items)}")
    return render_template(
        "admin/call_requests.html",
        items=items,
        current_status=status_filter,
    )


@admin_bp.route("/call-requests/<uuid:id>/status", methods=["POST"])
@login_required
@role_required("admin")
def call_request_update_status(id: UUID):
    """Updates status of a customer call request."""
    req = CallRequest.query.filter_by(id=id).first_or_404()
    new_status = request.form.get("status", "").strip()

    try:
        call_request_service.update_status(req, new_status, handler_user=current_user)
        db.session.commit()
        if new_status == "new":
            flash("Call request reopened successfully.", "success")
        else:
            flash(f"Call request marked as {new_status}.", "success")
    except InvalidCallRequestTransition as exc:
        flash(str(exc), "danger")

    return redirect(url_for("admin.call_requests_list", status=request.args.get("status")))


import json
import os
import time
import uuid
from datetime import datetime, timedelta, UTC

from flask import render_template, redirect, url_for, request, flash, abort, current_app, g
from flask_login import login_required, current_user
from sqlalchemy import select, func, or_
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

from app.admin import admin_bp
from app.admin.forms import TrekForm, StaffActionForm, UserSearchForm, StaffCreateForm
from app.auth.decorators import role_required, tenant_feature_required, trek_content_editor_required
from app.extensions import db
from app.models import (
    User, StaffProfile, Trek, Booking, Payment, AuditLog, Role,
    TrekItineraryDay, ItineraryElement, TrekMedia, CallRequest, Ticket,
    MessageCampaign, MessageCampaignRecipient
)
from app.services import (
    trek_service, auth_service, notification_service, history_service,
    id_codec, rbac_service, itinerary_service, media_service
)

PER_PAGE_DEFAULT = 20
PER_PAGE_MAX = 100


def _pagination_args():
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", PER_PAGE_DEFAULT, type=int), PER_PAGE_MAX)
    return max(page, 1), max(per_page, 1)


def _approved_staff_choices():
    rows = db.session.execute(
        select(StaffProfile).where(StaffProfile.approval_status == "approved")
    ).scalars().all()
    return [(0, "-- Unassigned --")] + [
        (s.id, s.user.name if s.user else f"Staff #{s.id}") for s in rows
    ]


def _dashboard_context():
    total_treks = db.session.scalar(select(func.count(Trek.id))) or 0
    open_treks = db.session.scalar(select(func.count(Trek.id)).where(Trek.status == "open")) or 0
    total_users = db.session.scalar(select(func.count(User.id))) or 0
    total_staff = db.session.scalar(select(func.count(StaffProfile.id))) or 0
    pending_staff_approvals = db.session.scalar(
        select(func.count(StaffProfile.id)).where(StaffProfile.approval_status == "pending")
    ) or 0
    total_bookings = db.session.scalar(select(func.count(Booking.id))) or 0
    confirmed_bookings = db.session.scalar(select(func.count(Booking.id)).where(Booking.status == "booked")) or 0
    pending_bookings = db.session.scalar(select(func.count(Booking.id)).where(Booking.status == "pending")) or 0

    total_revenue = db.session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0.0)).where(Payment.status.in_(["success", "paid"]))
    ) or 0.0

    thirty_days_ago = datetime.now(UTC) - timedelta(days=30)
    recent_revenue = db.session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0.0)).where(
            Payment.status.in_(["success", "paid"]),
            Payment.created_at >= thirty_days_ago,
        )
    ) or 0.0

    total_refunded = db.session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0.0)).where(Payment.status == "refunded")
    ) or 0.0

    new_call_requests = db.session.scalar(
        select(func.count(CallRequest.id)).where(CallRequest.status == "new")
    ) or 0
    total_call_requests = db.session.scalar(select(func.count(CallRequest.id))) or 0

    pending_manual_bookings = db.session.scalar(
        select(func.count(Booking.id)).where(Booking.booking_channel == "manual", Booking.status == "pending")
    ) or 0
    total_manual_bookings = db.session.scalar(
        select(func.count(Booking.id)).where(Booking.booking_channel == "manual")
    ) or 0

    open_tickets = db.session.scalar(
        select(func.count(Ticket.id)).where(Ticket.status == "open")
    ) or 0

    ongoing_campaigns = db.session.scalar(
        select(func.count(MessageCampaign.id)).where(MessageCampaign.status.in_(["draft", "tested"]))
    ) or 0
    sent_campaigns = db.session.scalar(
        select(func.count(MessageCampaign.id)).where(MessageCampaign.status == "sent")
    ) or 0
    total_campaigns = db.session.scalar(select(func.count(MessageCampaign.id))) or 0
    recipients_reached = db.session.scalar(
        select(func.coalesce(func.sum(MessageCampaign.recipient_count), 0)).where(MessageCampaign.status == "sent")
    ) or 0

    recent_payments = db.session.execute(
        select(Payment).order_by(Payment.created_at.desc()).limit(5)
    ).scalars().all()

    eight_weeks_ago = datetime.now(UTC) - timedelta(weeks=8)
    dialect_name = db.engine.dialect.name if hasattr(db, "engine") and db.engine else "postgresql"
    if dialect_name == "sqlite":
        week_col = func.strftime("%Y-W%W", Booking.booking_date).label("week")
    else:
        week_col = func.to_char(Booking.booking_date, 'YYYY-"W"IW').label("week")

    weekly_rows = db.session.execute(
        select(week_col, func.count(Booking.id))
        .where(Booking.booking_date >= eight_weeks_ago)
        .group_by("week")
        .order_by("week")
    ).all()

    status_rows = db.session.execute(
        select(Trek.status, func.count(Trek.id)).group_by(Trek.status)
    ).all()

    return {
        "stats": {
            "total_treks": total_treks,
            "open_treks": open_treks,
            "total_users": total_users,
            "total_staff": total_staff,
            "total_bookings": total_bookings,
            "confirmed_bookings": confirmed_bookings,
            "pending_bookings": pending_bookings,
            "total_revenue": float(total_revenue),
            "recent_revenue": float(recent_revenue),
            "total_refunded": float(total_refunded),
        },
        "notifications": {
            "new_call_requests": new_call_requests,
            "total_call_requests": total_call_requests,
            "pending_manual_bookings": pending_manual_bookings,
            "total_manual_bookings": total_manual_bookings,
            "open_tickets": open_tickets,
            "pending_staff_approvals": pending_staff_approvals,
            "total_pending_actions": (
                new_call_requests + pending_manual_bookings + open_tickets + pending_staff_approvals
            ),
        },
        "campaigns": {
            "ongoing": ongoing_campaigns,
            "sent": sent_campaigns,
            "total": total_campaigns,
            "recipients_reached": int(recipients_reached),
        },
        "recent_payments": recent_payments,
        "bookings_over_time": {
            "labels": [row[0] for row in weekly_rows],
            "data": [row[1] for row in weekly_rows],
        },
        "treks_by_status": {row[0]: row[1] for row in status_rows},
    }


@admin_bp.app_template_filter("pretty_json")
def pretty_json(value):
    if not value:
        return ""
    try:
        return json.dumps(json.loads(value), indent=2)
    except (TypeError, ValueError):
        return value


#  Dashboard
@admin_bp.route("/dashboard")
@login_required
@role_required("admin")
def dashboard():
    context = _dashboard_context()
    counts = history_service.get_dashboard_counts(current_user)
    context["counts"] = counts
    return render_template("admin/dashboard.html", **context)


# Treks
@admin_bp.route("/treks")
@login_required
@role_required("admin")
def treks_list():
    difficulty = request.args.get("difficulty", "")
    status = request.args.get("status", "")
    search = request.args.get("search", "")
    awaiting = request.args.get("awaiting", "")

    stmt = select(Trek)
    if difficulty:
        stmt = stmt.where(Trek.difficulty == difficulty)
    if status:
        stmt = stmt.where(Trek.status == status)
    if awaiting:
        stmt = stmt.where(Trek.status == "pending", Trek.assigned_staff_id.is_(None))
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Trek.name.ilike(like), Trek.location.ilike(like)))

    treks = db.session.execute(stmt.order_by(Trek.created_at.desc())).scalars().all()
    return render_template(
        "admin/treks_list.html",
        treks=treks,
        difficulty=difficulty,
        status=status,
        search=search,
        awaiting=awaiting,
    )


@admin_bp.route("/treks/new", methods=["GET", "POST"])
@login_required
@role_required("admin")
def trek_new():
    form = TrekForm()
    form.assigned_staff_id.choices = _approved_staff_choices()

    if form.validate_on_submit():
        trek = Trek(
            name=form.name.data,
            location=form.location.data,
            difficulty=form.difficulty.data,
            duration_days=form.duration_days.data,
            total_slots=form.total_slots.data,
            available_slots=form.total_slots.data,
            price=form.price.data,
            start_date=form.start_date.data,
            end_date=form.end_date.data,
            status="pending",
            description=form.description.data,
            itinerary_layout=form.itinerary_layout.data or "accordion",
            created_by_user_id=current_user.id,
        )
        if form.assigned_staff_id.data:
            trek.assigned_staff_id = form.assigned_staff_id.data

        if form.thumbnail.data:
            file = form.thumbnail.data
            filename = secure_filename(f"trek_{int(time.time())}_{file.filename}")
            upload_folder = os.path.join(current_app.root_path, "static", "uploads", "treks")
            os.makedirs(upload_folder, exist_ok=True)
            
            file_path = os.path.join(upload_folder, filename)
            file.save(file_path)
            
            trek.thumbnail_filename = filename

        db.session.add(trek)
        db.session.flush()
        db.session.add(AuditLog.record(current_user.id, "trek.create", "trek", trek.id))
        db.session.commit()
        flash(f"Trek '{trek.name}' created. Add itinerary and media below.", "success")
        return redirect(url_for("admin.trek_edit", trek_id=trek.id, tab="itinerary"))

    return render_template("admin/trek_form.html", form=form, trek=None)


@admin_bp.route("/treks/<hashid:trek_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def trek_edit(trek_id):
    # print(f"[DEBUG] hashid route {request.path}")
    trek = db.session.get(Trek, trek_id)
    if trek is None:
        abort(404)
        
    form = TrekForm(obj=trek)
    form.assigned_staff_id.choices = _approved_staff_choices()
    
    if request.method == "GET":
        form.assigned_staff_id.data = trek.assigned_staff_id or 0

    if form.validate_on_submit():
        is_newly_completed = (form.status.data == "completed" and trek.status != "completed")

        if form.available_slots.data is not None and form.available_slots.data > form.total_slots.data:
            flash("Available slots cannot exceed total slots.", "danger")
            return render_template("admin/trek_form.html", form=form, trek=trek)

        form.populate_obj(trek)
        
        if form.assigned_staff_id.data == 0:
            trek.assigned_staff_id = None

        if form.thumbnail.data:
            file = form.thumbnail.data
            filename = secure_filename(f"trek_{int(time.time())}_{file.filename}")
            upload_folder = os.path.join(current_app.root_path, "static", "uploads", "treks")
            os.makedirs(upload_folder, exist_ok=True)
            
            file_path = os.path.join(upload_folder, filename)
            file.save(file_path)
            
            trek.thumbnail_filename = filename

        if is_newly_completed:
            count = trek_service.complete_trek_bookings(trek)
            db.session.add(AuditLog.record(
                current_user.id, 
                "trek.complete_bookings", 
                "trek", 
                trek.id, 
                details=f"{count} bookings marked completed"
            ))

        db.session.add(AuditLog.record(current_user.id, "trek.update", "trek", trek.id))
        db.session.commit()
        
        flash("Trek updated successfully.", "success")
        return redirect(url_for("admin.trek_detail", trek_id=trek.id))

    itinerary = itinerary_service.get_itinerary(trek)
    active_tab = request.args.get("tab", "details").strip().lower()
    return render_template(
        "admin/trek_form.html",
        form=form,
        trek=trek,
        itinerary=itinerary,
        base_url=f"/admin/treks/{id_codec.encode(trek.id)}",
        active_tab=active_tab,
    )


@admin_bp.route("/treks/<hashid:trek_id>")
@login_required
@role_required("admin")
def trek_detail(trek_id):
    # print(f"[DEBUG] hashid route {request.path}")
    trek = db.session.get(Trek, trek_id)
    if trek is None:
        abort(404)

    staff_choices = _approved_staff_choices()

    legal_transitions = []
    for candidate in ("approved", "open", "closed", "completed"):
        try:
            trek_service.transition_status(Trek(status=trek.status), candidate, current_user)
            legal_transitions.append(candidate)
        except trek_service.InvalidTransitionError:
            continue

    has_active_booking = (db.session.scalar(
        select(func.count(Booking.id)).where(Booking.trek_id == trek.id, Booking.status != "cancelled")
    ) or 0) > 0

    return render_template(
        "admin/trek_detail.html", trek=trek, staff_choices=staff_choices,
        legal_transitions=legal_transitions, has_active_booking=has_active_booking,
    )


@admin_bp.route("/treks/<hashid:trek_id>/transition", methods=["POST"])
@login_required
@role_required("admin")
def trek_transition(trek_id):
    # print(f"[DEBUG] hashid route {request.path}")
    trek = db.session.get(Trek, trek_id)
    if trek is None:
        abort(404)

    new_status = request.form.get("status", "")
    try:
        trek_service.transition_status(trek, new_status, current_user)
    except trek_service.InvalidTransitionError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.trek_detail", trek_id=trek.id))

    db.session.add(AuditLog.record(
        current_user.id, "trek.transition", "trek", trek.id,
        details=json.dumps({"new_status": new_status}),
    ))
    db.session.commit()
    flash(f"Trek status changed to '{new_status}'.", "success")
    return redirect(url_for("admin.trek_detail", trek_id=trek.id))


@admin_bp.route("/treks/<hashid:trek_id>/assign-staff", methods=["POST"])
@login_required
@role_required("admin")
def assign_staff(trek_id):
    # print(f"[DEBUG] hashid route {request.path}")
    trek = db.session.get(Trek, trek_id)
    if trek is None:
        abort(404)

    staff_id = request.form.get("staff_id", type=int)
    staff = db.session.get(StaffProfile, staff_id) if staff_id else None
    if not staff or staff.approval_status != "approved":
        flash("Only an approved staff member can be assigned to a trek.", "danger")
        return redirect(url_for("admin.trek_detail", trek_id=trek.id))

    trek.assigned_staff_id = staff.id
    db.session.add(AuditLog.record(
        current_user.id, "trek.assign_staff", "trek", trek.id,
        details=json.dumps({"staff_id": staff.id}),
    ))
    db.session.commit()
    flash(f"Assigned staff to '{trek.name}'.", "success")
    return redirect(url_for("admin.trek_detail", trek_id=trek.id))


@admin_bp.route("/treks/<hashid:trek_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def trek_delete(trek_id):
    # print(f"[DEBUG] hashid route {request.path}")
    trek = db.session.get(Trek, trek_id)
    if trek is None:
        abort(404)

    has_active_booking = (db.session.scalar(
        select(func.count(Booking.id)).where(Booking.trek_id == trek.id, Booking.status != "cancelled")
    ) or 0) > 0
    if has_active_booking:
        flash("Cannot delete a trek with active bookings.", "danger")
        return redirect(url_for("admin.trek_detail", trek_id=trek.id))

    trek_id_for_log, trek_name = trek.id, trek.name
    media_service.delete_all_for_trek(trek)
    db.session.delete(trek)
    db.session.add(AuditLog.record(current_user.id, "trek.delete", "trek", trek_id_for_log))
    db.session.commit()
    flash(f"Trek '{trek_name}' deleted.", "success")
    return redirect(url_for("admin.treks_list"))


#Bookings & Payments 
@admin_bp.route("/bookings")
@login_required
@role_required("admin")
def bookings():
    status_filter = request.args.get("status")
    bookings_list = history_service.get_booking_history(current_user, status=status_filter)
    return render_template("admin/bookings_list.html", bookings=bookings_list, status=status_filter)


@admin_bp.route("/bookings/<hashid:booking_id>")
@login_required
@role_required("admin")
def booking_detail(booking_id):
    # print(f"[DEBUG] hashid route {request.path}")
    booking = db.session.get(Booking, booking_id)
    if booking is None:
        abort(404)
    if booking.status == "pending":
        from app.services import payment_service
        payment_service.sync_booking_payment(booking)
        db.session.refresh(booking)
    return render_template("admin/booking_detail.html", booking=booking)


@admin_bp.route("/bookings/<hashid:booking_id>/force-cancel", methods=["POST"])
@login_required
@role_required("admin")
def booking_force_cancel(booking_id):
    booking = db.session.get(Booking, booking_id)
    if not booking:
        abort(404)
    reason_note = (request.form.get("reason") or "").strip()
    if len(reason_note) < 10:
        flash("A cancellation explanation of at least 10 characters is required.", "danger")
        return redirect(url_for("admin.booking_detail", booking_id=booking.id))

    try:
        from app.services import payment_service
        payment_service.cancel_booking(booking, current_user, "staff_force_cancel", note=reason_note)
        flash(f"Booking #{booking.id} force-cancelled. 100% refund initiated.", "success")
    except Exception as err:
        flash(f"Cancellation error: {str(err)}", "danger")

    return redirect(url_for("admin.booking_detail", booking_id=booking.id))


@admin_bp.route("/payments")
@login_required
@role_required("admin")
def payments_list():
    status = request.args.get("status", "")
    raw_booking_id = request.args.get("booking_id")
    booking_id = id_codec.decode(raw_booking_id) if raw_booking_id else None
    if booking_id is None and raw_booking_id:
        try:
            booking_id = int(raw_booking_id)
        except ValueError:
            booking_id = None

    stmt = select(Payment)
    if status:
        stmt = stmt.where(Payment.status == status)
    if booking_id:
        stmt = stmt.where(Payment.booking_id == booking_id)

    payments = db.session.execute(stmt.order_by(Payment.created_at.desc())).scalars().all()
    return render_template(
        "admin/payments_list.html", payments=payments, status=status, booking_id=booking_id
    )


@admin_bp.route("/payments/<hashid:payment_id>")
@login_required
@role_required("admin")
def payment_detail(payment_id):
    # print(f"[DEBUG] hashid route {request.path}")
    payment = db.session.get(Payment, payment_id)
    if payment is None:
        abort(404)
    return render_template("admin/payment_detail.html", payment=payment)


# Staff, Users, Reports, Audit Log & Profile 
@admin_bp.route("/staff")
@login_required
@role_required("admin")
def staff_list():
    tab = request.args.get("approval_status", "all")
    stmt = select(StaffProfile)
    if tab in ("pending", "approved", "blacklisted"):
        stmt = stmt.where(StaffProfile.approval_status == tab)

    staff_rows = db.session.execute(stmt).scalars().all()
    pending_count = db.session.scalar(
        select(func.count(StaffProfile.id)).where(StaffProfile.approval_status == "pending")
    ) or 0
    roles = Role.query.filter_by(organization_id=g.organization_id).order_by(Role.name.asc()).all()

    return render_template(
        "admin/staff_list.html",
        staff_rows=staff_rows,
        active_tab=tab,
        pending_count=pending_count,
        roles=roles,
    )


@admin_bp.route("/staff/new", methods=["GET", "POST"])
@login_required
@role_required("admin")
def staff_create():
    roles = Role.query.filter_by(organization_id=g.organization_id).order_by(Role.name.asc()).all()
    form = StaffCreateForm()
    form.role_id.choices = [(str(r.id), r.name) for r in roles]

    if form.validate_on_submit():
        clean_email = form.email.data.strip().lower()
        existing = User.query.filter_by(email=clean_email, organization_id=g.organization_id).first()
        if existing:
            flash(f"A user with email '{clean_email}' already exists in this organization.", "danger")
            return render_template("admin/staff_create.html", form=form, roles=roles)

        import uuid
        try:
            role_uuid = uuid.UUID(str(form.role_id.data))
        except (ValueError, TypeError):
            role_uuid = None

        selected_role = Role.query.filter_by(id=role_uuid, organization_id=g.organization_id).first() if role_uuid else None
        if not selected_role:
            flash("Selected role is invalid.", "danger")
            return render_template("admin/staff_create.html", form=form, roles=roles)

        try:
            from app.services.auth_service import validate_mobile
            cc, nat, _ = validate_mobile(form.mobile_country_code.data, form.mobile_number.data)
        except Exception as exc:
            flash(str(exc), "danger")
            return render_template("admin/staff_create.html", form=form, roles=roles)

        user = User(
            name=form.name.data.strip(),
            email=clean_email,
            role="staff",
            mobile_country_code=cc,
            mobile_number=nat,
            terms_accepted_at=datetime.now(UTC),
            organization_id=g.organization_id,
        )
        temp_password = auth_service.issue_staff_default_password(user)
        db.session.add(user)
        db.session.flush()

        staff_profile = StaffProfile(
            user_id=user.id,
            role_id=selected_role.id,
            contact_details=form.contact_details.data,
            approval_status="approved",
            approved_by_admin_id=current_user.id,
            approved_at=datetime.now(UTC),
            organization_id=g.organization_id,
        )
        db.session.add(staff_profile)
        db.session.flush()

        rbac_service.assign_role(current_user, staff_profile, selected_role)
        auth_service.record_consent(user, "email", "transactional", granted=True, source="admin_direct_add")

        try:
            notification_service.send_staff_approved_email(user)
        except Exception as exc:
            current_app.logger.warning(f"Could not dispatch staff approved email: {exc}")

        db.session.add(AuditLog.record(current_user.id, "staff.create_direct", "staff_profile", staff_profile.id, f"Directly created staff member {user.name} ({user.email})"))
        db.session.commit()

        flash(f"Staff member {user.name} added and approved with role '{selected_role.name}'. Default login password: {temp_password}", "success")
        return redirect(url_for("admin.staff_list"))

    return render_template("admin/staff_create.html", form=form, roles=roles)


@admin_bp.route("/staff/<hashid:staff_id>")
@login_required
@role_required("admin")
def staff_detail(staff_id):
    # print(f"[DEBUG] hashid route {request.path}")
    staff = db.session.get(StaffProfile, staff_id)
    if staff is None:
        abort(404)
    roles = Role.query.filter_by(organization_id=g.organization_id).order_by(Role.name.asc()).all()
    return render_template("admin/staff_detail.html", staff=staff, roles=roles)


@admin_bp.route("/staff/<hashid:staff_id>/approve", methods=["POST"])
@login_required
@role_required("admin")
def staff_approve(staff_id):
    staff = db.session.get(StaffProfile, staff_id)
    if staff is None:
        abort(404)

    role_id_str = request.form.get("role_id", "").strip()
    # print(f"[DEBUG] staff approve staff_id={staff_id} role_id={role_id_str}")
    if not role_id_str:
        flash("A role must be assigned when approving staff.", "danger")
        return redirect(url_for("admin.staff_list"))

    try:
        role_uuid = uuid.UUID(role_id_str)
    except (ValueError, TypeError):
        flash("Invalid role identifier provided.", "danger")
        return redirect(url_for("admin.staff_list"))

    role = Role.query.filter_by(id=role_uuid, organization_id=g.organization_id).first()
    if not role:
        flash("The selected role does not belong to this organization.", "danger")
        return redirect(url_for("admin.staff_list"))

    staff.approval_status = "approved"
    staff.approved_by_admin_id = current_user.id
    staff.approved_at = datetime.now(UTC)
    auth_service.issue_staff_default_password(staff.user)
    rbac_service.assign_role(current_user, staff, role)
    notification_service.send_staff_approved_email(staff.user)
    db.session.add(AuditLog.record(current_user.id, "staff.approve", "staff_profile", staff.id))
    db.session.commit()

    flash(f"Approved {staff.user.name} as Trek Staff with role '{role.name}'.", "success")
    return redirect(url_for("admin.staff_list"))


@admin_bp.route("/staff/<hashid:staff_id>/action", methods=["POST"])
@login_required
@role_required("admin")
def staff_action(staff_id):
    # print(f"[DEBUG] hashid route {request.path}")
    form = StaffActionForm()
    if not form.validate_on_submit() or form.action.data not in ("approve", "blacklist"):
        flash("Invalid or unrecognized staff action.", "danger")
        return redirect(url_for("admin.staff_list"))

    staff = db.session.get(StaffProfile, staff_id)
    if staff is None:
        abort(404)

    if form.action.data == "approve":
        role_id_str = request.form.get("role_id", "").strip()
        # print(f"[DEBUG] staff approve staff_id={staff_id} role_id={role_id_str}")
        if not role_id_str:
            flash("A role must be assigned when approving staff.", "danger")
            return redirect(url_for("admin.staff_list"))

        try:
            role_uuid = uuid.UUID(role_id_str)
        except (ValueError, TypeError):
            flash("Invalid role identifier provided.", "danger")
            return redirect(url_for("admin.staff_list"))

        role = Role.query.filter_by(id=role_uuid, organization_id=g.organization_id).first()
        if not role:
            flash("The selected role does not belong to this organization.", "danger")
            return redirect(url_for("admin.staff_list"))

        staff.approval_status = "approved"
        staff.approved_by_admin_id = current_user.id
        staff.approved_at = datetime.now(UTC)
        auth_service.issue_staff_default_password(staff.user)
        rbac_service.assign_role(current_user, staff, role)
        notification_service.send_staff_approved_email(staff.user)
        db.session.add(AuditLog.record(current_user.id, "staff.approve", "staff_profile", staff.id))
        flash(f"Approved {staff.user.name} as Trek Staff with role '{role.name}'.", "success")
    else:
        staff.approval_status = "blacklisted"
        staff.user.is_active = False
        db.session.add(AuditLog.record(current_user.id, "staff.blacklist", "staff_profile", staff.id))
        flash(f"Blacklisted {staff.user.name}.", "success")

    db.session.commit()
    return redirect(url_for("admin.staff_list"))


@admin_bp.route("/users")
@login_required
@role_required("admin")
def users_list():
    form = UserSearchForm(request.args)
    stmt = select(User)

    if form.search.data:
        like = f"%{form.search.data}%"
        stmt = stmt.where(or_(User.name.ilike(like), User.email.ilike(like)))
    if form.role.data:
        stmt = stmt.where(User.role == form.role.data)
    if form.is_active.data == "true":
        stmt = stmt.where(User.is_active.is_(True))
    elif form.is_active.data == "false":
        stmt = stmt.where(User.is_active.is_(False))

    users = db.session.execute(stmt.order_by(User.created_at.desc())).scalars().all()
    return render_template("admin/users_list.html", users=users, form=form)


@admin_bp.route("/users/<hashid:user_id>")
@login_required
@role_required("admin")
def user_detail(user_id):
    # print(f"[DEBUG] hashid route {request.path}")
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    bookings = db.session.execute(
        select(Booking).where(Booking.user_id == user.id).order_by(Booking.booking_date.desc())
    ).scalars().all()
    return render_template("admin/user_detail.html", user=user, bookings=bookings)


@admin_bp.route("/users/<hashid:user_id>/deactivate", methods=["POST"])
@login_required
@role_required("admin")
def user_deactivate(user_id):
    # print(f"[DEBUG] hashid route {request.path}")
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    if user.role == "admin":
        flash("Admin accounts cannot be deactivated here.", "warning")
        return redirect(request.referrer or url_for("admin.user_detail", user_id=user.id))

    user.is_active = not user.is_active
    action = "user.reactivate" if user.is_active else "user.deactivate"
    db.session.add(AuditLog.record(current_user.id, action, "user", user.id))
    db.session.commit()
    
    flash(f"{user.name} {'reactivated' if user.is_active else 'deactivated'}.", "success")
    return redirect(request.referrer or url_for("admin.user_detail", user_id=user.id))


@admin_bp.route("/reports")
@login_required
@role_required("admin")
def reports():
    return render_template("admin/reports.html", **_dashboard_context())


@admin_bp.route("/audit-log")
@login_required
@role_required("admin")
def audit_log():
    page, per_page = _pagination_args()
    pagination = db.paginate(
        select(AuditLog).order_by(AuditLog.created_at.desc()),
        page=page, per_page=per_page, error_out=False,
    )
    return render_template("admin/audit_log.html", pagination=pagination, page=page)


@admin_bp.route("/profile", methods=["GET", "POST"])
@login_required
@role_required("admin")
def profile():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if name:
            current_user.name = name

        if current_password or new_password or confirm_password:
            if not check_password_hash(current_user.password_hash, current_password):
                db.session.rollback()
                flash("Current password is incorrect.", "danger")
                return redirect(url_for("admin.profile"))
            if new_password != confirm_password or len(new_password) < 8:
                db.session.rollback()
                flash("New password must match and be at least 8 characters.", "danger")
                return redirect(url_for("admin.profile"))
            current_user.set_password(new_password)

        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("admin.profile"))

    return render_template("admin/profile.html")


@admin_bp.route("/profile/picture", methods=["POST"])
@login_required
@role_required("admin")
def upload_profile_picture():
    from app.trekker.forms import ProfilePictureForm
    form = ProfilePictureForm()

    if not form.validate_on_submit():
        for error_list in form.errors.values():
            for error in error_list:
                flash(error, "danger")
        return redirect(url_for("admin.profile"))

    file = form.picture.data

    magic_bytes = file.read(3)
    file.seek(0)
    if magic_bytes != b"\xff\xd8\xff":
        flash("Invalid file content. Uploaded file is not a genuine JPEG.", "danger")
        return redirect(url_for("admin.profile"))

    raw_filename = f"user_{current_user.id}_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
    filename = secure_filename(raw_filename)

    upload_folder = current_app.config.get("PROFILE_PIC_UPLOAD_FOLDER", "app/static/uploads/profile_pics")
    os.makedirs(upload_folder, exist_ok=True)
    file_path = os.path.join(upload_folder, filename)

    old_filename = current_user.profile_picture_filename

    file.save(file_path)

    if old_filename:
        old_file_path = os.path.join(upload_folder, old_filename)
        try:
            if os.path.exists(old_file_path):
                os.remove(old_file_path)
        except Exception as e:
            current_app.logger.warning(
                f"Failed to delete old profile picture {old_filename} for admin {current_user.id}: {e}"
            )

    current_user.profile_picture_filename = filename
    db.session.commit()

    flash("Profile picture updated successfully.", "success")
    return redirect(url_for("admin.profile"))


# Content endpoints for Trek Itinerary and Media (P10)
@admin_bp.route("/treks/<hashid:trek_id>/itinerary/layout", methods=["POST"])
@login_required
@role_required("admin")
@trek_content_editor_required
def admin_itinerary_layout_update(trek_id):
    trek = g.trek
    layout = request.form.get("itinerary_layout", "accordion").strip().lower()
    if layout in ("timeline", "magazine") and not g.organization.has_feature("advanced_itineraries"):
        flash("Advanced Itinerary layouts (Timeline & Magazine) require a Growth or Enterprise subscription plan.", "warning")
        return redirect(url_for("admin.trek_edit", trek_id=trek.id, _anchor="itinerary-builder"))

    if layout in ("accordion", "timeline", "magazine"):
        trek.itinerary_layout = layout
        db.session.commit()
        flash(f"Itinerary display layout updated to '{layout.title()}'.", "success")
    else:
        flash("Invalid itinerary layout selected.", "danger")
    return redirect(url_for("admin.trek_edit", trek_id=trek.id, _anchor="itinerary-builder"))


@admin_bp.route("/treks/<hashid:trek_id>/itinerary/days/add", methods=["POST"])
@login_required
@role_required("admin")
@trek_content_editor_required
def admin_itinerary_day_add(trek_id):
    trek = g.trek
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip() or None
    important_points = request.form.getlist("important_points[]") or request.form.get("important_points")
    if not title:
        flash("Day title is required.", "danger")
    else:
        try:
            itinerary_service.add_day(trek, title, description, important_points=important_points)
            db.session.commit()
            flash("Itinerary day added.", "success")
        except itinerary_service.ItineraryLimitError as exc:
            flash(str(exc), "danger")
    return redirect(url_for("admin.trek_edit", trek_id=trek.id, tab="itinerary"))


@admin_bp.route("/treks/<hashid:trek_id>/itinerary/days/<uuid:day_id>/update", methods=["POST"])
@login_required
@role_required("admin")
@trek_content_editor_required
def admin_itinerary_day_update(trek_id, day_id):
    day = TrekItineraryDay.query.filter_by(id=day_id, trek_id=g.trek.id).first_or_404()
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip() or None
    important_points = request.form.getlist("important_points[]") or request.form.get("important_points")
    if not title:
        flash("Day title is required.", "danger")
    else:
        itinerary_service.update_day(day, title, description, important_points=important_points)
        db.session.commit()
        flash("Day updated.", "success")
    return redirect(url_for("admin.trek_edit", trek_id=g.trek.id, tab="itinerary"))


@admin_bp.route("/treks/<hashid:trek_id>/itinerary/days/<uuid:day_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
@trek_content_editor_required
def admin_itinerary_day_delete(trek_id, day_id):
    day = TrekItineraryDay.query.filter_by(id=day_id, trek_id=g.trek.id).first_or_404()
    itinerary_service.delete_day(day)
    db.session.commit()
    flash("Day deleted.", "info")
    return redirect(url_for("admin.trek_edit", trek_id=g.trek.id, tab="itinerary"))


@admin_bp.route("/treks/<hashid:trek_id>/itinerary/days/<uuid:day_id>/elements/add", methods=["POST"])
@login_required
@role_required("admin")
@trek_content_editor_required
def admin_itinerary_element_add(trek_id, day_id):
    day = TrekItineraryDay.query.filter_by(id=day_id, trek_id=g.trek.id).first_or_404()
    el_type = request.form.get("element_type", "activity").strip()
    title = request.form.get("title", "").strip()
    desc = request.form.get("description", "").strip() or None
    time_of_day = request.form.get("time_of_day", "").strip() or None
    points = request.form.getlist("points[]") or request.form.get("points")
    if not title:
        flash("Item title is required.", "danger")
    else:
        try:
            itinerary_service.add_element(day, el_type, title, desc, time_of_day, points=points)
            db.session.commit()
            flash("Item added to day.", "success")
        except itinerary_service.InvalidElementTypeError as exc:
            flash(str(exc), "danger")
    return redirect(url_for("admin.trek_edit", trek_id=g.trek.id, tab="itinerary"))


@admin_bp.route("/treks/<hashid:trek_id>/itinerary/elements/<uuid:element_id>/update", methods=["POST"])
@login_required
@role_required("admin")
@trek_content_editor_required
def admin_itinerary_element_update(trek_id, element_id):
    element = ItineraryElement.query.join(TrekItineraryDay).filter(
        ItineraryElement.id == element_id, TrekItineraryDay.trek_id == g.trek.id
    ).first_or_404()
    el_type = request.form.get("element_type", element.element_type).strip()
    title = request.form.get("title", "").strip()
    desc = request.form.get("description", "").strip() or None
    time_of_day = request.form.get("time_of_day", "").strip() or None
    points = request.form.getlist("points[]") or request.form.get("points")
    if not title:
        flash("Title is required.", "danger")
    else:
        try:
            itinerary_service.update_element(element, el_type, title, desc, time_of_day, points=points)
            db.session.commit()
            flash("Item updated.", "success")
        except itinerary_service.InvalidElementTypeError as exc:
            flash(str(exc), "danger")
    return redirect(url_for("admin.trek_edit", trek_id=g.trek.id, tab="itinerary"))


@admin_bp.route("/treks/<hashid:trek_id>/itinerary/elements/<uuid:element_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
@trek_content_editor_required
def admin_itinerary_element_delete(trek_id, element_id):
    element = ItineraryElement.query.join(TrekItineraryDay).filter(
        ItineraryElement.id == element_id, TrekItineraryDay.trek_id == g.trek.id
    ).first_or_404()
    itinerary_service.delete_element(element)
    db.session.commit()
    flash("Item deleted.", "info")
    return redirect(url_for("admin.trek_edit", trek_id=g.trek.id, tab="itinerary"))


@admin_bp.route("/treks/<hashid:trek_id>/itinerary/elements/<uuid:element_id>/move", methods=["POST"])
@login_required
@role_required("admin")
@trek_content_editor_required
def admin_itinerary_element_move(trek_id, element_id):
    element = ItineraryElement.query.join(TrekItineraryDay).filter(
        ItineraryElement.id == element_id, TrekItineraryDay.trek_id == g.trek.id
    ).first_or_404()
    direction = request.form.get("direction", "up").strip()
    itinerary_service.move_element(element, direction)
    db.session.commit()
    return redirect(url_for("admin.trek_edit", trek_id=g.trek.id, tab="itinerary"))


@admin_bp.route("/treks/<hashid:trek_id>/media/upload", methods=["POST"])
@login_required
@role_required("admin")
@trek_content_editor_required
def admin_media_upload(trek_id):
    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        f = request.files.get("file")
        if f:
            files = [f]

    group_select = request.form.get("group_select", "").strip()
    custom_group = request.form.get("custom_group", "").strip()
    group = custom_group if group_select == "__custom__" else (group_select or request.form.get("group", "")).strip()

    base_caption = request.form.get("caption", "").strip()

    if not files or all(not f.filename for f in files):
        flash("No valid image files selected.", "danger")
        return redirect(url_for("admin.trek_edit", trek_id=g.trek.id, tab="gallery"))

    uploaded_count = 0
    errors = []

    for file in files:
        if not file or not file.filename:
            continue
        if group and base_caption:
            full_caption = f"[{group}] {base_caption}"
        elif group:
            full_caption = f"[{group}]"
        else:
            full_caption = base_caption or None

        try:
            media_service.save_upload(g.trek, file, current_user, full_caption)
            uploaded_count += 1
        except media_service.InvalidMediaError as exc:
            errors.append(f"{file.filename}: {str(exc)}")

    if uploaded_count > 0:
        db.session.commit()
        flash(f"{uploaded_count} photo(s) uploaded successfully.", "success")
    if errors:
        flash(f"Some files could not be uploaded: {'; '.join(errors)}", "danger")

    return redirect(url_for("admin.trek_edit", trek_id=g.trek.id, tab="gallery"))


@admin_bp.route("/treks/<hashid:trek_id>/media/<uuid:media_id>/caption", methods=["POST"])
@login_required
@role_required("admin")
@trek_content_editor_required
def admin_media_caption(trek_id, media_id):
    media = TrekMedia.query.filter_by(id=media_id, trek_id=g.trek.id).first_or_404()
    caption = request.form.get("caption", "").strip() or None
    media_service.update_caption(media, caption)
    db.session.commit()
    flash("Caption updated.", "success")
    return redirect(url_for("admin.trek_edit", trek_id=g.trek.id, tab="gallery"))


@admin_bp.route("/treks/<hashid:trek_id>/media/<uuid:media_id>/move", methods=["POST"])
@login_required
@role_required("admin")
@trek_content_editor_required
def admin_media_move(trek_id, media_id):
    media = TrekMedia.query.filter_by(id=media_id, trek_id=g.trek.id).first_or_404()
    direction = request.form.get("direction", "left").strip()
    media_service.move(media, direction)
    db.session.commit()
    return redirect(url_for("admin.trek_edit", trek_id=g.trek.id, tab="gallery"))


@admin_bp.route("/treks/<hashid:trek_id>/media/<uuid:media_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
@trek_content_editor_required
def admin_media_delete(trek_id, media_id):
    media = TrekMedia.query.filter_by(id=media_id, trek_id=g.trek.id).first_or_404()
    media_service.delete_media(media)
    db.session.commit()
    flash("Image deleted.", "info")
    return redirect(url_for("admin.trek_edit", trek_id=g.trek.id, tab="gallery"))


# --- Milestone P13: Backups & Disaster Recovery ---
@admin_bp.route("/backups", methods=["GET"])
@login_required
@role_required("admin")
@tenant_feature_required("backups")
def admin_backups():
    from app.models.tenant_backup import TenantBackup
    backups = TenantBackup.query.filter_by(organization_id=g.organization_id).order_by(TenantBackup.created_at.desc()).all()
    return render_template("admin/backups.html", backups=backups)


@admin_bp.route("/backups/trigger", methods=["POST"])
@login_required
@role_required("admin")
@tenant_feature_required("backups")
def admin_trigger_backup():
    from app.services import backup_service
    try:
        backup = backup_service.create_tenant_backup(
            organization_id=g.organization_id,
            triggered_by_user_id=current_user.id,
        )
        flash("Tenant backup generated successfully.", "success")
    except Exception as e:
        flash(f"Failed to generate backup: {str(e)}", "danger")
    return redirect(url_for("admin.admin_backups"))


@admin_bp.route("/backups/<uuid:backup_id>/download", methods=["GET"])
@login_required
@role_required("admin")
@tenant_feature_required("backups")
def admin_download_backup(backup_id):
    from flask import send_file
    from app.models.tenant_backup import TenantBackup
    backup = TenantBackup.query.filter_by(id=backup_id, organization_id=g.organization_id).first_or_404()

    if not os.path.exists(backup.storage_location):
        abort(404)

    org_slug = g.organization.name.lower().replace(" ", "_")
    dl_name = f"tenant_{org_slug}_backup_{backup.created_at.strftime('%Y%m%d_%H%M%S')}.json.gz"
    return send_file(backup.storage_location, as_attachment=True, download_name=dl_name, mimetype="application/gzip")

import json
import os
import time
from datetime import datetime, timedelta, UTC

from flask import render_template, redirect, url_for, request, flash, abort, current_app
from flask_login import login_required, current_user
from sqlalchemy import select, func, or_
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

from app.admin import admin_bp
from app.admin.forms import TrekForm, StaffActionForm, UserSearchForm
from app.auth.decorators import role_required
from app.extensions import db
from app.models import User, StaffProfile, Trek, Booking, Payment, AuditLog
from app.services import trek_service, auth_service, notification_service, history_service

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
    total_users = db.session.scalar(select(func.count(User.id))) or 0
    total_staff = db.session.scalar(select(func.count(StaffProfile.id))) or 0
    total_bookings = db.session.scalar(select(func.count(Booking.id))) or 0

    eight_weeks_ago = datetime.now(UTC) - timedelta(weeks=8)
    weekly_rows = db.session.execute(
        select(func.strftime("%Y-W%W", Booking.booking_date).label("week"), func.count(Booking.id))
        .where(Booking.booking_date >= eight_weeks_ago)
        .group_by("week")
        .order_by("week")
    ).all()

    status_rows = db.session.execute(
        select(Trek.status, func.count(Trek.id)).group_by(Trek.status)
    ).all()

    return {
        "stats": {
            "total_treks": total_treks, "total_users": total_users,
            "total_staff": total_staff, "total_bookings": total_bookings,
        },
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

    stmt = select(Trek)
    if difficulty:
        stmt = stmt.where(Trek.difficulty == difficulty)
    if status:
        stmt = stmt.where(Trek.status == status)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Trek.name.ilike(like), Trek.location.ilike(like)))

    treks = db.session.execute(stmt.order_by(Trek.created_at.desc())).scalars().all()
    return render_template(
        "admin/treks_list.html", treks=treks, difficulty=difficulty, status=status, search=search
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
        flash(f"Trek '{trek.name}' created.", "success")
        return redirect(url_for("admin.trek_detail", trek_id=trek.id))

    return render_template("admin/trek_form.html", form=form, trek=None)


@admin_bp.route("/treks/<int:trek_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def trek_edit(trek_id):
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

    return render_template("admin/trek_form.html", form=form, trek=trek)


@admin_bp.route("/treks/<int:trek_id>")
@login_required
@role_required("admin")
def trek_detail(trek_id):
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


@admin_bp.route("/treks/<int:trek_id>/transition", methods=["POST"])
@login_required
@role_required("admin")
def trek_transition(trek_id):
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


@admin_bp.route("/treks/<int:trek_id>/assign-staff", methods=["POST"])
@login_required
@role_required("admin")
def assign_staff(trek_id):
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


@admin_bp.route("/treks/<int:trek_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def trek_delete(trek_id):
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


@admin_bp.route("/bookings/<int:booking_id>")
@login_required
@role_required("admin")
def booking_detail(booking_id):
    booking = db.session.get(Booking, booking_id)
    if booking is None:
        abort(404)
    return render_template("admin/booking_detail.html", booking=booking)


@admin_bp.route("/payments")
@login_required
@role_required("admin")
def payments_list():
    status = request.args.get("status", "")
    booking_id = request.args.get("booking_id", type=int)

    stmt = select(Payment)
    if status:
        stmt = stmt.where(Payment.status == status)
    if booking_id:
        stmt = stmt.where(Payment.booking_id == booking_id)

    payments = db.session.execute(stmt.order_by(Payment.created_at.desc())).scalars().all()
    return render_template(
        "admin/payments_list.html", payments=payments, status=status, booking_id=booking_id
    )


@admin_bp.route("/payments/<int:payment_id>")
@login_required
@role_required("admin")
def payment_detail(payment_id):
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

    return render_template(
        "admin/staff_list.html", staff_rows=staff_rows, active_tab=tab, pending_count=pending_count
    )


@admin_bp.route("/staff/<int:staff_id>")
@login_required
@role_required("admin")
def staff_detail(staff_id):
    staff = db.session.get(StaffProfile, staff_id)
    if staff is None:
        abort(404)
    return render_template("admin/staff_detail.html", staff=staff)


@admin_bp.route("/staff/<int:staff_id>/action", methods=["POST"])
@login_required
@role_required("admin")
def staff_action(staff_id):
    form = StaffActionForm()
    if not form.validate_on_submit() or form.action.data not in ("approve", "blacklist"):
        flash("Invalid or unrecognized staff action.", "danger")
        return redirect(url_for("admin.staff_list"))

    staff = db.session.get(StaffProfile, staff_id)
    if staff is None:
        abort(404)

    if form.action.data == "approve":
        staff.approval_status = "approved"
        staff.approved_by_admin_id = current_user.id
        staff.approved_at = datetime.now(UTC)
        auth_service.issue_staff_default_password(staff.user)
        notification_service.send_staff_approved_email(staff.user)
        db.session.add(AuditLog.record(current_user.id, "staff.approve", "staff_profile", staff.id))
        flash(f"Approved {staff.user.name} as Trek Staff.", "success")
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


@admin_bp.route("/users/<int:user_id>")
@login_required
@role_required("admin")
def user_detail(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    bookings = db.session.execute(
        select(Booking).where(Booking.user_id == user.id).order_by(Booking.booking_date.desc())
    ).scalars().all()
    return render_template("admin/user_detail.html", user=user, bookings=bookings)


@admin_bp.route("/users/<int:user_id>/deactivate", methods=["POST"])
@login_required
@role_required("admin")
def user_deactivate(user_id):
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
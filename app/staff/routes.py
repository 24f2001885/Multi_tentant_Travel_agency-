
import os
import time

from flask import current_app, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from app.auth.decorators import approved_staff_required, role_required, staff_owns_trek
from app.extensions import db
from app.models import Booking, User
from app.services.trek_service import ALLOWED_TRANSITIONS, InvalidTransitionError, transition_status
from app.services import history_service, trek_service
from app.staff import staff_bp
from app.staff.forms import TrekSlotStatusForm
from app.trekker.forms import ProfilePictureForm


@staff_bp.route("/dashboard")
@login_required
@role_required("staff")
@approved_staff_required
def dashboard():
    profile = current_user.staff_profile
    treks = profile.treks if profile else []

    stats = history_service.get_dashboard_counts(current_user)

    chart_labels = []
    chart_values = []
    for trek in treks:
        count = Booking.query.filter(
            Booking.trek_id == trek.id,
            Booking.status.in_(("pending", "booked")),
        ).count()
        chart_labels.append(trek.name)
        chart_values.append(count)

    return render_template(
        "staff/dashboard.html",
        stats=stats,
        chart_labels=chart_labels,
        chart_values=chart_values,
        treks=treks,  
    )


@staff_bp.route("/treks/<int:trek_id>/manage")
@login_required
@role_required("staff")
@approved_staff_required
@staff_owns_trek
def trek_manage(trek_id):
    trek = g.trek
    form = TrekSlotStatusForm(available_slots=trek.available_slots)
    legal_transitions = sorted(ALLOWED_TRANSITIONS.get("staff", {}).get(trek.status, set()))
    return render_template(
        "staff/trek_manage.html", trek=trek, form=form, legal_transitions=legal_transitions
    )


@staff_bp.route("/treks/<int:trek_id>/update-slots", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@staff_owns_trek
def update_slots(trek_id):
    trek = g.trek
    form = TrekSlotStatusForm()

    if not form.validate_on_submit():
        flash("Please enter a valid slot count.", "danger")
        return redirect(url_for("staff.trek_manage", trek_id=trek.id))

    if form.available_slots.data > trek.total_slots:
        flash("Available slots cannot exceed total slots.", "danger")
        return redirect(url_for("staff.trek_manage", trek_id=trek.id))

    trek.available_slots = form.available_slots.data
    db.session.commit()
    flash("Slot count updated.", "success")
    return redirect(url_for("staff.trek_manage", trek_id=trek.id))


@staff_bp.route("/treks/<int:trek_id>/transition", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@staff_owns_trek
def transition(trek_id):
    trek = g.trek
    new_status = request.form.get("new_status", "")

    try:
        transition_status(trek, new_status, actor=current_user)
        
        if new_status == 'completed':
            trek_service.complete_trek_bookings(trek)
            
        db.session.commit()
        flash(f"Trek status updated to '{new_status}'.", "success")
    except InvalidTransitionError as exc:
        db.session.rollback()
        flash(str(exc), "danger")

    return redirect(url_for("staff.trek_manage", trek_id=trek.id))


@staff_bp.route("/treks/<int:trek_id>/participants")
@login_required
@role_required("staff")
@approved_staff_required
@staff_owns_trek
def participants(trek_id):
    trek = g.trek
    all_bookings = history_service.get_booking_history(current_user)
    
    bookings = [b for b in all_bookings if b.trek_id == trek.id]
    
    return render_template("staff/participants.html", trek=trek, bookings=bookings)


@staff_bp.route("/profile", methods=["GET", "POST"])
@login_required
@role_required("staff")
@approved_staff_required
def profile():
    staff_profile = current_user.staff_profile

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        contact_details = request.form.get("contact_details", "").strip()
        current_password = request.form.get("current_password")
        new_password = request.form.get("new_password")

        if name:
            current_user.name = name
        staff_profile.contact_details = contact_details

        if new_password:
            if not current_password or not check_password_hash(
                current_user.password_hash, current_password
            ):
                flash("Current password is incorrect.", "danger")
                return redirect(url_for("staff.profile"))
            current_user.password_hash = generate_password_hash(new_password)

        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("staff.profile"))

    return render_template("staff/profile.html", profile=staff_profile)


@staff_bp.route("/profile/picture", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
def upload_profile_picture():
    form = ProfilePictureForm()
    
    if not form.validate_on_submit():
        for error_list in form.errors.values():
            for error in error_list:
                flash(error, "danger")
        return redirect(url_for("staff.profile"))
    
    file = form.picture.data
    
    magic_bytes = file.read(3)
    file.seek(0)  
    if magic_bytes != b"\xff\xd8\xff":
        flash("Invalid file content. Uploaded file is not a genuine JPEG.", "danger")
        return redirect(url_for("staff.profile"))
    
    raw_filename = f"user_{current_user.id}_{int(time.time())}.jpg"
    filename = secure_filename(raw_filename)
    
    upload_folder = current_app.config["PROFILE_PIC_UPLOAD_FOLDER"]
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
                f"Failed to delete old profile picture {old_filename} for user {current_user.id}: {e}"
            )
            
    
    current_user.profile_picture_filename = filename
    db.session.commit()
    
    flash("Profile picture updated successfully.", "success")
    return redirect(url_for("staff.profile"))
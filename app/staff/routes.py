
import os
import time
import uuid

from flask import abort, current_app, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from app.auth.decorators import (
    approved_staff_required, requires_capability, role_required,
    staff_owns_trek, tenant_feature_required, trek_content_editor_required
)
from app.extensions import db
from app.models import Booking, User, Trek, TrekItineraryDay, ItineraryElement, TrekMedia
from app.services.trek_service import ALLOWED_TRANSITIONS, InvalidTransitionError, transition_status
from app.services import history_service, trek_service, itinerary_service, media_service, id_codec
from app.staff import staff_bp
from app.staff.forms import TrekSlotStatusForm, StaffTrekCreateForm
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

    created_treks = (
        Trek.query.filter_by(created_by_user_id=current_user.id)
        .order_by(Trek.created_at.desc())
        .all()
        if current_user.has_capability("treks.create")
        else []
    )

    return render_template(
        "staff/dashboard.html",
        stats=stats,
        chart_labels=chart_labels,
        chart_values=chart_values,
        treks=treks,
        created_treks=created_treks,
    )


@staff_bp.route("/treks/<hashid:trek_id>/manage")
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.manage_assigned")
@staff_owns_trek
def trek_manage(trek_id):
    # print(f"[DEBUG] staff route {request.path} caps={session.get('capability_keys')}")
    trek = g.trek
    form = TrekSlotStatusForm(available_slots=trek.available_slots)
    legal_transitions = sorted(ALLOWED_TRANSITIONS.get("staff", {}).get(trek.status, set()))
    return render_template(
        "staff/trek_manage.html", trek=trek, form=form, legal_transitions=legal_transitions
    )


@staff_bp.route("/treks/<hashid:trek_id>/update-slots", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.manage_assigned")
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

    prev_slots = trek.available_slots
    trek.available_slots = form.available_slots.data
    db.session.commit()

    if trek.available_slots > prev_slots:
        from app.services import waitlist_service
        waitlist_service.on_slots_freed(trek)

    flash("Slot count updated.", "success")
    return redirect(url_for("staff.trek_manage", trek_id=trek.id))


@staff_bp.route("/bookings/manual", methods=["GET", "POST"], endpoint="manual_booking")
@staff_bp.route("/bookings/manual", methods=["GET", "POST"], endpoint="manual_booking_new")
@login_required
@role_required("staff")
@approved_staff_required
@tenant_feature_required("manual_bookings")
@requires_capability("bookings.manual_create")
def manual_booking_new():
    org = g.organization
    if not org or not org.manual_booking_enabled:
        flash("Manual bookings are not enabled for this organization.", "danger")
        return redirect(url_for("staff.dashboard")), 403

    from app.models.trek import Trek
    from app.services import auth_service, booking_service
    from decimal import Decimal

    if request.method == "POST":
        raw_trek_id = request.form.get("trek_id")
        from app.services.id_codec import decode
        try:
            trek_id = int(raw_trek_id) if raw_trek_id is not None else None
        except (ValueError, TypeError):
            trek_id = decode(str(raw_trek_id)) if raw_trek_id else None

        trek = Trek.query.filter_by(id=trek_id, organization_id=org.id).first()
        if not trek or trek.status != "open":
            flash("Selected trek is not open.", "danger")
            return redirect(url_for("staff.manual_booking_new"))

        customer_email = request.form.get("customer_email", "").strip().lower()
        customer_name = request.form.get("customer_name", "").strip()
        cc = request.form.get("mobile_country_code", "+91").strip()
        phone = request.form.get("mobile_number", "").strip()
        stated_amount_str = request.form.get("stated_amount")
        note = (request.form.get("note") or request.form.get("notes") or "").strip()

        target_user = auth_service.create_user_for_manual_booking(
            name=customer_name or "Trekker",
            email=customer_email,
            mobile_country_code=cc,
            mobile_number=phone or "9999999999",
        )

        names = request.form.getlist("participant_name")
        ages = request.form.getlist("participant_age")
        participants = []
        for i in range(len(names)):
            n = names[i].strip() if i < len(names) else ""
            a = ages[i].strip() if i < len(ages) else None
            if n:
                participants.append({"name": n, "age": int(a) if a else None})

        slot_count = request.form.get("slot_count", type=int) or 1
        if not participants or len(participants) < slot_count:
            count_needed = max(slot_count, len(participants) or 1)
            participants = [{"name": target_user.name, "age": None} for _ in range(count_needed)]

        if stated_amount_str:
            stated_amount = Decimal(stated_amount_str)
        else:
            stated_amount = Decimal(str(trek.price)) * len(participants)

        try:
            booking = booking_service.create_manual_booking(
                creator=current_user,
                trek=trek,
                target_user=target_user,
                participants=participants,
                stated_amount=stated_amount,
                note=note,
            )
            flash(f"Manual booking #{booking.id} created and queued for Tenant Admin sign-off.", "success")
            return redirect(url_for("staff.dashboard"))
        except Exception as e:
            flash(f"Error creating manual booking: {str(e)}", "danger")

    open_treks = Trek.query.filter_by(organization_id=org.id, status="open").all()
    return render_template("staff/manual_booking.html", treks=open_treks)


@staff_bp.route("/bookings/<hashid:booking_id>/force-cancel", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("bookings.force_cancel")
def staff_force_cancel(booking_id):
    booking = db.session.get(Booking, booking_id)
    if not booking or not booking.trek:
        abort(404)

    profile = getattr(current_user, "staff_profile", None)
    if not profile or booking.trek.assigned_staff_id != profile.id:
        abort(403)

    reason = (request.form.get("reason") or "").strip()
    if len(reason) < 10:
        flash("A cancellation explanation of at least 10 characters is required.", "danger")
        return redirect(url_for("staff.trek_manage", trek_id=booking.trek_id))

    from app.services import payment_service
    payment_service.cancel_booking(booking, current_user, "staff_force_cancel", note=reason)
    flash(f"Booking #{booking.id} force-cancelled. 100% refund initiated.", "success")
    return redirect(url_for("staff.trek_manage", trek_id=booking.trek_id))



@staff_bp.route("/treks/<hashid:trek_id>/transition", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.manage_assigned")
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


@staff_bp.route("/treks/<hashid:trek_id>/participants")
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("participants.view")
@staff_owns_trek
def participants(trek_id):
    # print(f"[DEBUG] staff route {request.path} caps={session.get('capability_keys')}")
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
    
    raw_filename = f"user_{current_user.id}_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
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


# Staff Trek Creation and Content Routes (P10)
@staff_bp.route("/treks/new", methods=["GET", "POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.create")
def trek_create():
    form = StaffTrekCreateForm()
    if form.validate_on_submit():
        trek = trek_service.create_trek_as_staff(current_user, form.data)
        db.session.commit()
        flash(f"Trek '{trek.name}' created! It is awaiting Admin assignment and approval.", "success")
        return redirect(url_for("staff.trek_content", trek_id=trek.id))
    return render_template("staff/trek_create.html", form=form)


@staff_bp.route("/treks/<hashid:trek_id>/content", methods=["GET"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.edit_content")
@trek_content_editor_required
def trek_content(trek_id):
    trek = g.trek
    itinerary = itinerary_service.get_itinerary(trek)
    base_url = f"/staff/treks/{id_codec.encode(trek.id)}/content"
    active_tab = request.args.get("tab", "itinerary").strip().lower()
    return render_template(
        "staff/trek_manage.html",
        trek=trek,
        itinerary=itinerary,
        base_url=base_url,
        can_edit_content=True,
        active_tab=active_tab,
    )


@staff_bp.route("/treks/<hashid:trek_id>/content/itinerary/layout", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.edit_content")
@trek_content_editor_required
def staff_itinerary_layout_update(trek_id):
    trek = g.trek
    layout = request.form.get("itinerary_layout", "accordion").strip().lower()
    if layout in ("timeline", "magazine") and not g.organization.has_feature("advanced_itineraries"):
        flash("Advanced Itinerary layouts (Timeline & Magazine) require a Growth or Enterprise subscription plan.", "warning")
        return redirect(url_for("staff.trek_content", trek_id=trek.id, _anchor="itinerary-builder"))

    if layout in ("accordion", "timeline", "magazine"):
        trek.itinerary_layout = layout
        db.session.commit()
        flash(f"Itinerary display layout updated to '{layout.title()}'.", "success")
    else:
        flash("Invalid itinerary layout selected.", "danger")
    return redirect(url_for("staff.trek_content", trek_id=trek.id, _anchor="itinerary-builder"))


@staff_bp.route("/treks/<hashid:trek_id>/content/itinerary/days/add", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.edit_content")
@trek_content_editor_required
def staff_itinerary_day_add(trek_id):
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
    return redirect(url_for("staff.trek_content", trek_id=trek.id))


@staff_bp.route("/treks/<hashid:trek_id>/content/itinerary/days/<uuid:day_id>/update", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.edit_content")
@trek_content_editor_required
def staff_itinerary_day_update(trek_id, day_id):
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
    return redirect(url_for("staff.trek_content", trek_id=g.trek.id))


@staff_bp.route("/treks/<hashid:trek_id>/content/itinerary/days/<uuid:day_id>/delete", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.edit_content")
@trek_content_editor_required
def staff_itinerary_day_delete(trek_id, day_id):
    day = TrekItineraryDay.query.filter_by(id=day_id, trek_id=g.trek.id).first_or_404()
    itinerary_service.delete_day(day)
    db.session.commit()
    flash("Day deleted.", "info")
    return redirect(url_for("staff.trek_content", trek_id=g.trek.id))


@staff_bp.route("/treks/<hashid:trek_id>/content/itinerary/days/<uuid:day_id>/elements/add", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.edit_content")
@trek_content_editor_required
def staff_itinerary_element_add(trek_id, day_id):
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
    return redirect(url_for("staff.trek_content", trek_id=g.trek.id))


@staff_bp.route("/treks/<hashid:trek_id>/content/itinerary/elements/<uuid:element_id>/update", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.edit_content")
@trek_content_editor_required
def staff_itinerary_element_update(trek_id, element_id):
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
    return redirect(url_for("staff.trek_content", trek_id=g.trek.id))


@staff_bp.route("/treks/<hashid:trek_id>/content/itinerary/elements/<uuid:element_id>/delete", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.edit_content")
@trek_content_editor_required
def staff_itinerary_element_delete(trek_id, element_id):
    element = ItineraryElement.query.join(TrekItineraryDay).filter(
        ItineraryElement.id == element_id, TrekItineraryDay.trek_id == g.trek.id
    ).first_or_404()
    itinerary_service.delete_element(element)
    db.session.commit()
    flash("Item deleted.", "info")
    return redirect(url_for("staff.trek_content", trek_id=g.trek.id))


@staff_bp.route("/treks/<hashid:trek_id>/content/itinerary/elements/<uuid:element_id>/move", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.edit_content")
@trek_content_editor_required
def staff_itinerary_element_move(trek_id, element_id):
    element = ItineraryElement.query.join(TrekItineraryDay).filter(
        ItineraryElement.id == element_id, TrekItineraryDay.trek_id == g.trek.id
    ).first_or_404()
    direction = request.form.get("direction", "up").strip()
    itinerary_service.move_element(element, direction)
    db.session.commit()
    return redirect(url_for("staff.trek_content", trek_id=g.trek.id))


@staff_bp.route("/treks/<hashid:trek_id>/content/media/upload", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.edit_content")
@trek_content_editor_required
def staff_media_upload(trek_id):
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
        return redirect(url_for("staff.trek_content", trek_id=g.trek.id, tab="gallery"))

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

    return redirect(url_for("staff.trek_content", trek_id=g.trek.id, tab="gallery"))


@staff_bp.route("/treks/<hashid:trek_id>/content/media/<uuid:media_id>/caption", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.edit_content")
@trek_content_editor_required
def staff_media_caption(trek_id, media_id):
    media = TrekMedia.query.filter_by(id=media_id, trek_id=g.trek.id).first_or_404()
    caption = request.form.get("caption", "").strip() or None
    media_service.update_caption(media, caption)
    db.session.commit()
    flash("Caption updated.", "success")
    return redirect(url_for("staff.trek_content", trek_id=g.trek.id))


@staff_bp.route("/treks/<hashid:trek_id>/content/media/<uuid:media_id>/move", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.edit_content")
@trek_content_editor_required
def staff_media_move(trek_id, media_id):
    media = TrekMedia.query.filter_by(id=media_id, trek_id=g.trek.id).first_or_404()
    direction = request.form.get("direction", "left").strip()
    media_service.move(media, direction)
    db.session.commit()
    return redirect(url_for("staff.trek_content", trek_id=g.trek.id))


@staff_bp.route("/treks/<hashid:trek_id>/content/media/<uuid:media_id>/delete", methods=["POST"])
@login_required
@role_required("staff")
@approved_staff_required
@requires_capability("treks.edit_content")
@trek_content_editor_required
def staff_media_delete(trek_id, media_id):
    media = TrekMedia.query.filter_by(id=media_id, trek_id=g.trek.id).first_or_404()
    media_service.delete_media(media)
    db.session.commit()
    flash("Image deleted.", "info")
    return redirect(url_for("staff.trek_content", trek_id=g.trek.id))
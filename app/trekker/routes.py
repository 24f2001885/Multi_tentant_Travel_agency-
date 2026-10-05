
import os
import time
from flask import render_template, request, redirect, url_for, flash, abort, current_app
from flask_login import login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from app.trekker import trekker_bp
from app.auth.decorators import role_required
from app.trekker.forms import BookingForm, ProfileForm, ProfilePictureForm
from app.extensions import db
from app.models import Trek, Booking
from app.services import booking_service, payment_service, history_service
from app.services.booking_service import TrekUnavailableError, DuplicateActiveBookingError


@trekker_bp.route("/dashboard")
@login_required
@role_required("trekker")
def dashboard():
    difficulty = request.args.get("difficulty")
    location = request.args.get("location")

    query = Trek.query.filter(Trek.status == "open", Trek.available_slots > 0)
    if difficulty:
        query = query.filter(Trek.difficulty == difficulty)
    if location:
        query = query.filter(Trek.location.ilike(f"%{location}%"))

    treks = query.order_by(Trek.start_date.asc()).all()
    return render_template("trekker/browse.html", treks=treks)


@trekker_bp.route("/treks/<hashid:trek_id>")
@login_required
@role_required("trekker")
def trek_detail(trek_id):
    trek = Trek.query.get_or_404(trek_id)
    can_book = trek.status == "open" and trek.available_slots > 0
    return render_template("trekker/trek_detail.html", trek=trek, can_book=can_book)


@trekker_bp.route("/treks/<hashid:trek_id>/book", methods=["GET"])
@login_required
@role_required("trekker")
def booking_review(trek_id):
    trek = Trek.query.get_or_404(trek_id)
    form = BookingForm()
    return render_template("trekker/booking_review.html", trek=trek, form=form)


@trekker_bp.route("/treks/<hashid:trek_id>/book", methods=["POST"])
@login_required
@role_required("trekker")
def book(trek_id):
    form = BookingForm()
    if not form.validate_on_submit():
        flash("Invalid submission, please try again.", "danger")
        return redirect(url_for("trekker.booking_review", trek_id=trek_id))

    try:
        booking = booking_service.initiate_booking(current_user.id, trek_id)
    except TrekUnavailableError:
        flash("This trek is no longer available.", "danger")
        return redirect(url_for("trekker.trek_detail", trek_id=trek_id))
    except DuplicateActiveBookingError:
        flash("You already have an active booking for this trek.", "danger")
        return redirect(url_for("trekker.trek_detail", trek_id=trek_id))

    # kick off the Razorpay order now that the booking row exists
    payment_service.create_gateway_order(booking)
    return redirect(url_for("trekker.payment", booking_id=booking.id))


@trekker_bp.route("/bookings")
@login_required
@role_required("trekker")
def bookings_list():
    status = request.args.get("status")
    
    bookings = history_service.get_booking_history(current_user, status=status)
    
    bookings.sort(key=lambda b: b.booking_date, reverse=True)
    
    return render_template("trekker/bookings_list.html", bookings=bookings)


@trekker_bp.route("/bookings/<hashid:booking_id>")
@login_required
@role_required("trekker")
def booking_detail(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id:
        abort(403)
    return render_template("trekker/booking_detail.html", booking=booking)


@trekker_bp.route("/bookings/<hashid:booking_id>/cancel", methods=["POST"])
@login_required
@role_required("trekker")
def cancel_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id:
        abort(403)
    if booking.status != "pending":
        flash("Only unpaid, pending bookings can be cancelled.", "warning")
        return redirect(url_for("trekker.booking_detail", booking_id=booking.id))

    booking.status = "cancelled"
    db.session.commit()
    flash("Booking cancelled successfully.", "success")
    return redirect(url_for("trekker.bookings_list"))


@trekker_bp.route("/profile", methods=["GET", "POST"])
@login_required
@role_required("trekker")
def profile():
    form = ProfileForm(obj=current_user)
    pic_form = ProfilePictureForm()  # for the picture-upload widget on this page
    
    if form.validate_on_submit():
        current_user.name = form.name.data
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("trekker.profile"))

    if request.method == "POST" and "new_password" in request.form:
        new_password = request.form.get("new_password")
        if new_password:
            current_password = request.form.get("current_password")
            if not current_password or not check_password_hash(
                current_user.password_hash, current_password
            ):
                flash("Current password is incorrect.", "danger")
            else:
                current_user.password_hash = generate_password_hash(new_password)
                db.session.commit()
                flash("Password updated.", "success")
            return redirect(url_for("trekker.profile"))

    return render_template("trekker/profile.html", form=form, pic_form=pic_form)


# Profile Picture Upload 
@trekker_bp.route("/profile/picture", methods=["POST"])
@login_required
@role_required("trekker")
def upload_profile_picture():
    form = ProfilePictureForm()
    
    if not form.validate_on_submit():
        for error_list in form.errors.values():
            for error in error_list:
                flash(error, "danger")
        return redirect(url_for("trekker.profile"))
    
    file = form.picture.data
    
    # Content-sniffing: Read first 3 bytes to verify JPEG magic number
    magic_bytes = file.read(3)
    file.seek(0)  # Reset stream position after peeking
    if magic_bytes != b"\xff\xd8\xff":
        flash("Invalid file content. Uploaded file is not a genuine JPEG.", "danger")
        return redirect(url_for("trekker.profile"))
    
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
    return redirect(url_for("trekker.profile"))

# Payment Routes

@trekker_bp.route("/treks/bookings/<hashid:booking_id>/payment")
@login_required
@role_required("trekker")
def payment(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id or booking.status != "pending":
        abort(403)

    order = booking.payments[-1] if booking.payments else None
    if order is None:
        abort(404)

    return render_template(
        "trekker/payment.html",
        booking=booking,
        order_id=order.gateway_order_id,
        razorpay_key=current_app.config["RAZORPAY_KEY_ID"],
        amount=int(order.amount * 100),
    )


@trekker_bp.route("/payment-result/<hashid:booking_id>")
@login_required
@role_required("trekker")
def payment_result(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id:
        abort(403)

    if booking.status == "booked":
        state = "success"
    elif booking.status == "cancelled" and booking.payment_status == "failed":
        state = "failed"
    elif booking.status == "cancelled" and booking.payment_status == "refunded":
        state = "slot_lost"
    else:
        state = "pending"

    return render_template("trekker/payment_result.html", booking=booking, state=state)

import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from flask import render_template, request, redirect, url_for, flash, abort, current_app, jsonify, Response
from flask_login import login_required, current_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from app.trekker import trekker_bp, trekker_root_bp
from app.auth.decorators import role_required
from app.trekker.forms import (
    BookingForm,
    ProfileForm,
    ProfilePictureForm,
    PasswordChangeForm,
    DeleteAccountForm,
    NotificationPreferencesForm,
)
from app.extensions import csrf, db
from app.models import Trek, Booking, AuditLog
from app.models.notification_consent import NotificationConsent
from app.models.notification_preference import NotificationPreference
from app.services import booking_service, payment_service, history_service, auth_service
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
    from app.services import itinerary_service, review_service
    itinerary = itinerary_service.get_itinerary(trek)
    avg_rating, review_count = review_service.average_rating(trek)
    reviews_page = review_service.list_reviews(trek, page=1, per_page=10)
    can_review, user_booking, review_reason = review_service.user_can_review_trek(current_user, trek)
    existing_user_review = review_service.get_user_trek_review(current_user, trek)
    return render_template(
        "trekker/trek_detail.html",
        trek=trek,
        itinerary=itinerary,
        avg_rating=avg_rating,
        review_count=review_count,
        reviews=reviews_page.items,
        can_book=can_book,
        can_review=can_review,
        user_booking=user_booking,
        review_reason=review_reason,
        existing_user_review=existing_user_review,
    )


@trekker_bp.route("/treks/<hashid:trek_id>/book", methods=["GET"])
@login_required
@role_required("trekker")
def booking_review(trek_id):
    trek = Trek.query.get_or_404(trek_id)
    form = BookingForm()
    return render_template("trekker/booking_review.html", trek=trek, form=form)


@trekker_bp.route("/treks/<hashid:trek_id>/validate-coupon", methods=["POST"])
@login_required
@role_required("trekker")
@csrf.exempt
def validate_coupon_ajax(trek_id):
    trek = Trek.query.get_or_404(trek_id)
    data = request.get_json(silent=True) or request.form
    code = (data.get("coupon_code") or "").strip()
    try:
        slot_count = max(1, int(data.get("slot_count", 1)))
    except (ValueError, TypeError):
        slot_count = 1

    from app.services import coupon_service
    base_amount = Decimal(str(trek.price)) * Decimal(str(slot_count))
    try:
        coupon = coupon_service.validate_coupon(code, current_user, trek, base_amount)
        discount = coupon_service.compute_discount(coupon, base_amount)
        final_total = max(Decimal("1.00"), base_amount - discount)
        return jsonify({
            "valid": True,
            "code": coupon.code,
            "discount": float(discount),
            "discount_type": coupon.discount_type,
            "discount_value": float(coupon.discount_value),
            "base_total": float(base_amount),
            "new_total": float(final_total),
            "message": f"Coupon '{coupon.code}' applied! Saving ₹{discount:.2f}",
        }), 200
    except coupon_service.CouponInvalidError as err:
        return jsonify({"valid": False, "error": str(err)}), 400
    except Exception as exc:
        return jsonify({"valid": False, "error": f"Invalid coupon: {str(exc)}"}), 400


@trekker_bp.route("/treks/<hashid:trek_id>/book", methods=["POST"])
@login_required
@role_required("trekker")
def book(trek_id):
    trek = Trek.query.get_or_404(trek_id)
    coupon_code = request.form.get("coupon_code", "").strip()

    names = request.form.getlist("participant_name")
    ages = request.form.getlist("participant_age")

    participants = []
    for i in range(len(names)):
        n = names[i].strip() if i < len(names) else ""
        a = ages[i].strip() if i < len(ages) else None
        if n:
            participants.append({"name": n, "age": int(a) if a else None})

    if not participants:
        participants = [{"name": current_user.name, "age": None}]

    from app.services import booking_service, coupon_service
    from app.services.booking_service import (
        DuplicateActiveBookingError,
        InvalidParticipantsError,
        RouteNotLinkedError,
        TrekUnavailableError,
    )

    try:
        booking = booking_service.initiate_booking(
            user_id=current_user.id,
            trek_id=trek.id,
            participants=participants,
            coupon_code=coupon_code if coupon_code else None,
        )
        return redirect(url_for("trekker.payment", booking_id=booking.id))
    except TrekUnavailableError:
        flash("This trek no longer has enough available slots.", "danger")
    except DuplicateActiveBookingError:
        flash("You already have an active booking for this trek.", "danger")
    except RouteNotLinkedError:
        flash("Online bookings are temporarily unavailable for this trek.", "warning")
    except coupon_service.CouponInvalidError as err:
        flash(f"Coupon error: {str(err)}", "danger")
    except InvalidParticipantsError as err:
        flash(f"Participant error: {str(err)}", "danger")
    except Exception as err:
        flash(f"Booking error: {str(err)}", "danger")

    return redirect(url_for("trekker.booking_review", trek_id=trek.id))


@trekker_bp.route("/treks/<hashid:trek_id>/waitlist", methods=["POST"])
@trekker_root_bp.route("/treks/<hashid:trek_id>/waitlist", methods=["POST"])
@login_required
@role_required("trekker")
def join_waitlist(trek_id):
    trek = Trek.query.get_or_404(trek_id)
    slots = request.form.get("requested_slot_count", 1, type=int)

    from app.services import waitlist_service
    try:
        waitlist_service.join_waitlist(current_user, trek, requested_slot_count=slots)
        flash("You have been added to the waitlist. We will notify you if slots open up!", "success")
    except Exception as err:
        flash(f"Could not join waitlist: {str(err)}", "danger")

    return redirect(url_for("trekker.trek_detail", trek_id=trek.id))


@trekker_bp.route("/waitlist/<uuid:entry_id>/leave", methods=["POST"])
@trekker_root_bp.route("/waitlist/<uuid:entry_id>/leave", methods=["POST"])
@login_required
@role_required("trekker")
def leave_waitlist(entry_id):
    from app.models.waitlist_entry import WaitlistEntry
    from app.services import waitlist_service
    entry = WaitlistEntry.query.get_or_404(entry_id)
    if entry.user_id != current_user.id:
        abort(403)

    waitlist_service.leave_waitlist(entry, current_user)
    flash("You have left the waitlist.", "info")
    return redirect(url_for("trekker.trek_detail", trek_id=entry.trek_id))


@trekker_bp.route("/bookings")
@trekker_root_bp.route("/bookings")
@login_required
@role_required("trekker")
def bookings_list():
    status = request.args.get("status")
    bookings = history_service.get_booking_history(current_user, status=status)
    bookings.sort(key=lambda b: b.booking_date, reverse=True)
    return render_template("trekker/bookings_list.html", bookings=bookings)


@trekker_bp.route("/bookings/<hashid:booking_id>")
@trekker_root_bp.route("/bookings/<hashid:booking_id>")
@login_required
@role_required("trekker")
def booking_detail(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id:
        abort(403)

    from app.services import refund_service, review_service, trek_service
    from app.models.invoice import Invoice
    refund_pct, refund_amt = refund_service.preview_refund(booking)
    invoice = Invoice.query.filter_by(booking_id=booking.id).first()
    can_review = review_service.can_review(current_user, booking)
    contact = trek_service.get_visible_contacts(booking.trek, booking)

    return render_template(
        "trekker/booking_detail.html",
        booking=booking,
        refund_pct=refund_pct,
        refund_amt=refund_amt,
        invoice=invoice,
        can_review=can_review,
        contact=contact,
    )


@trekker_bp.route("/bookings/<hashid:booking_id>/review", methods=["GET", "POST"])
@trekker_root_bp.route("/bookings/<hashid:booking_id>/review", methods=["GET", "POST"])
@login_required
@role_required("trekker")
def review_new(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id:
        abort(403)

    from app.services import review_service
    from app.trekker.forms import ReviewForm

    if not review_service.can_review(current_user, booking):
        flash("This booking cannot be reviewed.", "warning")
        return redirect(url_for("trekker.booking_detail", booking_id=booking.id))

    form = ReviewForm()
    if form.validate_on_submit():
        try:
            review_service.create_review(
                user=current_user,
                booking=booking,
                rating=int(form.rating.data),
                comment=form.comment.data,
            )
            db.session.commit()
            flash("Thank you for your review!", "success")
            return redirect(url_for("trekker.booking_detail", booking_id=booking.id))
        except Exception as exc:
            db.session.rollback()
            flash(f"Review error: {exc}", "danger")

    return render_template("trekker/review_form.html", booking=booking, form=form)


@trekker_bp.route("/bookings/<hashid:booking_id>/cancel", methods=["POST"])
@trekker_root_bp.route("/bookings/<hashid:booking_id>/cancel", methods=["POST"])
@login_required
@role_required("trekker")
def cancel_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id:
        abort(403)

    from app.services import payment_service
    try:
        payment_service.cancel_booking(booking, current_user, reason="customer_requested")
        flash("Your booking has been cancelled and refund processed per policy.", "success")
    except Exception as err:
        flash(f"Cancellation error: {str(err)}", "danger")

    return redirect(url_for("trekker.bookings_list"))


@trekker_bp.route("/bookings/<hashid:booking_id>/invoice")
@login_required
@role_required("trekker")
def download_invoice(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id:
        abort(403)

    from app.models.invoice import Invoice
    from app.services import invoice_service
    invoice = Invoice.query.filter_by(booking_id=booking.id).first_or_404()
    pdf_bytes = invoice_service.get_invoice_pdf_bytes(invoice)
    if not pdf_bytes:
        flash("Invoice PDF is currently generating or unavailable.", "warning")
        return redirect(url_for("trekker.booking_detail", booking_id=booking.id))

    from flask import Response
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={invoice.invoice_number}.pdf"},
    )



def _get_notification_settings_context(user):
    channels = ("email", "whatsapp", "sms")
    consents_map = {}
    prefs_map = {}

    for ch in channels:
        pref = NotificationPreference.query.filter_by(
            user_id=user.id, channel=ch
        ).first()
        prefs_map[ch] = pref.is_enabled if pref else True

        c_tx = NotificationConsent.query.filter_by(
            user_id=user.id, channel=ch, consent_type="transactional"
        ).first()
        c_mkt = NotificationConsent.query.filter_by(
            user_id=user.id, channel=ch, consent_type="marketing"
        ).first()

        consents_map[f"{ch}_tx"] = c_tx
        consents_map[f"{ch}_mkt"] = c_mkt

    return channels, prefs_map, consents_map


def _save_notification_preferences(user, form):
    channels = ("email", "whatsapp", "sms")
    for ch in channels:
        en_val = getattr(form, f"{ch}_enabled").data
        pref = NotificationPreference.query.filter_by(
            user_id=user.id, channel=ch
        ).first()
        if not pref:
            pref = NotificationPreference(
                organization_id=user.organization_id,
                user_id=user.id,
                channel=ch,
                is_enabled=en_val,
            )
            db.session.add(pref)
        else:
            pref.is_enabled = en_val

        tx_val = getattr(form, f"{ch}_transactional").data
        auth_service.record_consent(
            user, ch, "transactional", granted=tx_val, source="preferences"
        )

        mkt_val = getattr(form, f"{ch}_marketing").data
        auth_service.record_consent(
            user, ch, "marketing", granted=mkt_val, source="preferences"
        )

    db.session.commit()


@trekker_bp.route("/profile", methods=["GET", "POST"])
@login_required
@role_required("trekker")
def profile():
    return redirect(url_for("trekker.settings", tab="profile"))


@trekker_bp.route("/settings", methods=["GET", "POST"])
@login_required
@role_required("trekker")
def settings():
    active_tab = request.args.get("tab", "profile")
    channels, prefs_map, consents_map = _get_notification_settings_context(current_user)

    notif_form = NotificationPreferencesForm()
    profile_form = ProfileForm()
    pwd_form = PasswordChangeForm()
    pic_form = ProfilePictureForm()
    delete_form = DeleteAccountForm()

    if request.method == "POST":
        form_action = request.form.get("form_action", "profile")

        if form_action == "profile":
            if profile_form.validate_on_submit():
                current_user.name = profile_form.name.data.strip()
                if profile_form.mobile_country_code.data:
                    current_user.mobile_country_code = profile_form.mobile_country_code.data.strip()
                if profile_form.mobile_number.data:
                    current_user.mobile_number = profile_form.mobile_number.data.strip()
                db.session.commit()
                flash("Profile details updated successfully.", "success")
                return redirect(url_for("trekker.settings", tab="profile"))
            else:
                for err_list in profile_form.errors.values():
                    for err in err_list:
                        flash(err, "danger")
                active_tab = "profile"

        elif form_action == "password":
            if pwd_form.validate_on_submit():
                if not check_password_hash(current_user.password_hash, pwd_form.current_password.data):
                    flash("Current password is incorrect.", "danger")
                else:
                    current_user.password_hash = generate_password_hash(pwd_form.new_password.data)
                    db.session.commit()
                    flash("Password updated successfully.", "success")
                    return redirect(url_for("trekker.settings", tab="security"))
            else:
                for err_list in pwd_form.errors.values():
                    for err in err_list:
                        flash(err, "danger")
                active_tab = "security"

        elif form_action == "notifications":
            if notif_form.validate_on_submit():
                _save_notification_preferences(current_user, notif_form)
                flash("Notification preferences updated successfully.", "success")
                return redirect(url_for("trekker.settings", tab="notifications"))
            active_tab = "notifications"

    # Pre-populate forms on GET
    if request.method == "GET":
        profile_form.name.data = current_user.name
        profile_form.mobile_country_code.data = current_user.mobile_country_code
        profile_form.mobile_number.data = current_user.mobile_number if current_user.mobile_number != "0000000000" else ""

        notif_form.email_enabled.data = prefs_map["email"]
        notif_form.email_transactional.data = consents_map["email_tx"].is_active if consents_map["email_tx"] else False
        notif_form.email_marketing.data = consents_map["email_mkt"].is_active if consents_map["email_mkt"] else False

        notif_form.whatsapp_enabled.data = prefs_map["whatsapp"]
        notif_form.whatsapp_transactional.data = consents_map["whatsapp_tx"].is_active if consents_map["whatsapp_tx"] else False
        notif_form.whatsapp_marketing.data = consents_map["whatsapp_mkt"].is_active if consents_map["whatsapp_mkt"] else False

        notif_form.sms_enabled.data = prefs_map["sms"]
        notif_form.sms_transactional.data = consents_map["sms_tx"].is_active if consents_map["sms_tx"] else False
        notif_form.sms_marketing.data = consents_map["sms_mkt"].is_active if consents_map["sms_mkt"] else False

    from app.models import Payment, Ticket
    bookings_count = Booking.query.filter_by(user_id=current_user.id).count()
    payments_count = Payment.query.join(Booking, Payment.booking_id == Booking.id).filter(Booking.user_id == current_user.id).count()
    tickets_count = Ticket.query.filter_by(user_id=current_user.id).count()
    active_consents_count = NotificationConsent.query.filter_by(user_id=current_user.id, is_active=True).count()

    return render_template(
        "trekker/settings.html",
        active_tab=active_tab,
        profile_form=profile_form,
        pwd_form=pwd_form,
        pic_form=pic_form,
        notif_form=notif_form,
        delete_form=delete_form,
        consents_map=consents_map,
        bookings_count=bookings_count,
        payments_count=payments_count,
        tickets_count=tickets_count,
        active_consents_count=active_consents_count,
    )


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
        return redirect(request.referrer or url_for("trekker.settings", tab="profile"))
    
    file = form.picture.data
    
    # Content-sniffing: Read first 3 bytes to verify JPEG magic number
    magic_bytes = file.read(3)
    file.seek(0)  # Reset stream position after peeking
    if magic_bytes != b"\xff\xd8\xff":
        flash("Invalid file content. Uploaded file is not a genuine JPEG.", "danger")
        return redirect(request.referrer or url_for("trekker.settings", tab="profile"))
    
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
    return redirect(request.referrer or url_for("trekker.settings", tab="profile"))


@trekker_bp.route("/profile/picture/delete", methods=["POST"])
@login_required
@role_required("trekker")
def delete_profile_picture():
    user = current_user
    if user.profile_picture_filename:
        upload_folder = current_app.config.get("PROFILE_PIC_UPLOAD_FOLDER", "app/static/uploads/profile_pics")
        file_path = os.path.join(upload_folder, user.profile_picture_filename)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            current_app.logger.warning(f"Failed to delete picture {user.profile_picture_filename}: {e}")
        user.profile_picture_filename = None
        db.session.commit()
        flash("Profile picture removed successfully.", "success")
    return redirect(request.referrer or url_for("trekker.settings", tab="profile"))


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


@trekker_bp.route("/payment-result/<hashid:booking_id>", methods=["GET", "POST"])
@login_required
@role_required("trekker")
def payment_result(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id:
        abort(403)

    from app.services import payment_service

    if request.method == "POST":
        pay_id = request.form.get("razorpay_payment_id")
        ord_id = request.form.get("razorpay_order_id")
        sig = request.form.get("razorpay_signature")
        if ord_id and pay_id:
            payment_service.verify_and_confirm_payment(
                order_id=ord_id,
                payment_id=pay_id,
                signature=sig,
                org_id=booking.organization_id,
            )
            db.session.refresh(booking)

    if booking.status == "pending":
        payment_service.sync_booking_payment(booking)
        db.session.refresh(booking)

    if booking.status == "booked":
        state = "success"
    elif booking.status == "cancelled" and booking.payment_status == "failed":
        state = "failed"
    elif booking.status == "cancelled" and booking.payment_status == "refunded":
        state = "slot_lost"
    else:
        state = "pending"

    return render_template("trekker/payment_result.html", booking=booking, state=state)


@trekker_bp.route("/notification-preferences", methods=["GET", "POST"])
@login_required
@role_required("trekker")
def notification_preferences():
    """Notification preferences and consent management for trekkers (legacy route)."""
    form = NotificationPreferencesForm()

    if request.method == "POST" and form.validate_on_submit():
        _save_notification_preferences(current_user, form)
        flash("Notification preferences updated successfully.", "success")
        return redirect(url_for("trekker.settings", tab="notifications"))

    return redirect(url_for("trekker.settings", tab="notifications"))


@trekker_bp.route("/data-export", methods=["GET"])
@login_required
@role_required("trekker")
def export_data():
    """Exports all stored personal and operational data for the current trekker in structured JSON format."""
    from app.models import Payment, Ticket

    user = current_user
    bookings = Booking.query.filter_by(user_id=user.id).order_by(Booking.booking_date.desc()).all()
    payments = Payment.query.join(Booking, Payment.booking_id == Booking.id).filter(Booking.user_id == user.id).order_by(Payment.created_at.desc()).all()
    tickets = Ticket.query.filter_by(user_id=user.id).order_by(Ticket.created_at.desc()).all()
    consents = NotificationConsent.query.filter_by(user_id=user.id).all()
    prefs = NotificationPreference.query.filter_by(user_id=user.id).all()

    export_payload = {
        "export_metadata": {
            "platform": "TMA Trekking Platform",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "user_id": user.id,
            "organization_id": str(user.organization_id) if user.organization_id else None,
        },
        "user_profile": {
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "mobile_country_code": user.mobile_country_code,
            "mobile_number": user.mobile_number,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "terms_accepted_at": user.terms_accepted_at.isoformat() if user.terms_accepted_at else None,
            "has_profile_picture": user.has_profile_picture,
        },
        "saved_communication_preferences": [
            {"channel": p.channel, "is_enabled": p.is_enabled} for p in prefs
        ],
        "consents_and_opt_ins": [
            {
                "channel": c.channel,
                "consent_type": c.consent_type,
                "is_active": c.is_active,
                "granted_at": c.granted_at.isoformat() if c.granted_at else None,
                "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
            }
            for c in consents
        ],
        "bookings": [
            {
                "booking_id": b.id,
                "trek_id": b.trek_id,
                "trek_name": b.trek.name if b.trek else None,
                "slot_count": b.slot_count,
                "total_price": str(b.total_price) if b.total_price is not None else "0.00",
                "status": b.status,
                "payment_status": b.payment_status,
                "booking_date": b.booking_date.isoformat() if b.booking_date else None,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in bookings
        ],
        "payments": [
            {
                "payment_id": p.id,
                "booking_id": p.booking_id,
                "amount": str(p.amount) if p.amount is not None else "0.00",
                "currency": p.currency,
                "status": p.status,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in payments
        ],
        "support_tickets": [
            {
                "ticket_id": t.id,
                "subject": t.subject,
                "status": t.status,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in tickets
        ],
    }

    json_str = json.dumps(export_payload, indent=2, default=str)
    filename = f"tma_user_data_{user.id}_{int(time.time())}.json"
    return Response(
        json_str,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@trekker_bp.route("/account/delete", methods=["POST"])
@login_required
@role_required("trekker")
def delete_account():
    """
    Permanently anonymizes trekker identity, revokes all consents,
    clears profile picture from disk, deactivates account, and logs out the user.
    """
    confirm_phrase = request.form.get("confirm_phrase", "").strip()
    if confirm_phrase.upper() != "DELETE":
        flash("Confirmation text must match 'DELETE' exactly to confirm account deletion.", "danger")
        return redirect(url_for("trekker.settings", tab="danger"))

    user = current_user
    user_id = user.id

    # 1. Clean profile picture from disk if exists
    if user.profile_picture_filename:
        upload_folder = current_app.config.get("PROFILE_PIC_UPLOAD_FOLDER", "app/static/uploads/profile_pics")
        pic_path = os.path.join(upload_folder, user.profile_picture_filename)
        try:
            if os.path.exists(pic_path):
                os.remove(pic_path)
        except Exception as e:
            current_app.logger.warning(f"Could not remove avatar for user {user_id}: {e}")
        user.profile_picture_filename = None

    # 2. Anonymize user personal data (GDPR/DPDP Right to Erasure)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    user.name = "Deleted User"
    user.email = f"deleted_{user_id}_{now_ts}@anonymized.local"
    user.mobile_country_code = "+91"
    user.mobile_number = "0000000000"
    user.is_active = False

    # 3. Revoke all notification consents & disable notification preferences
    NotificationConsent.query.filter_by(user_id=user_id).update(
        {"revoked_at": datetime.now(timezone.utc)},
        synchronize_session=False,
    )
    NotificationPreference.query.filter_by(user_id=user_id).update(
        {"is_enabled": False},
        synchronize_session=False,
    )

    # 4. Record audit trail
    db.session.add(
        AuditLog.record(
            user_id,
            "user.delete_account",
            "user",
            user_id,
            "User self-initiated account deletion and data erasure request",
        )
    )
    db.session.commit()

    # 5. Terminate session and redirect to login page
    logout_user()
    flash("Your account and personal data have been deleted successfully.", "info")
    return redirect(url_for("auth.login"))

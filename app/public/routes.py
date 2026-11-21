"""
Public, anonymous-safe routes for discovery, legal documentation, and call requests (TDR v2 §5.4; PDR v2 §5.4, §5.13).
"""

from urllib.parse import urlparse
from flask import abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.auth.routes import _role_dashboard_url
from app.extensions import db, limiter
from app.public import public_bp
from app.services import call_request_service, trek_service
from app.services.auth_service import InvalidMobileError


def _is_safe_referrer(referrer: str) -> bool:
    if not referrer:
        return False
    parsed = urlparse(referrer)
    req_host = request.host.split(":")[0].lower()
    ref_host = (parsed.netloc or "").split(":")[0].lower()
    return not ref_host or ref_host == req_host


@public_bp.route("/", endpoint="index")
@public_bp.route("/", endpoint="landing")
def index():
    """Tenant landing page for anonymous visitors, or dashboard redirect for authenticated users."""
    # print(f"[DEBUG] public route {request.path} org={getattr(g, 'organization_id', None)}")
    if current_user.is_authenticated:
        return redirect(_role_dashboard_url(current_user.role))

    featured_treks = trek_service.list_open_treks()[:6]
    return render_template("trekker/landing.html", featured_treks=featured_treks)


@public_bp.route("/treks")
@public_bp.route("/browse")
def browse():
    """Public discovery route to browse and filter open treks."""
    difficulty = request.args.get("difficulty")
    location = request.args.get("location")
    treks = trek_service.list_open_treks(difficulty=difficulty, location=location)
    return render_template(
        "trekker/browse.html",
        treks=treks,
        difficulty=difficulty,
        location=location,
    )


@public_bp.route("/treks/<hashid:id>")
def trek_detail(id: int):
    """Public detail page for an individual trek. Pending/approved treks return 404."""
    trek = trek_service.get_public_trek(id)
    if trek is None:
        abort(404)
    from app.services import itinerary_service, review_service
    itinerary = itinerary_service.get_itinerary(trek)
    avg_rating, review_count = review_service.average_rating(trek)
    reviews_page = review_service.list_reviews(trek, page=1, per_page=10)
    can_book = trek.status == "open" and trek.available_slots > 0

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


@public_bp.route("/treks/<hashid:id>/reviews/submit", methods=["POST"])
@login_required
def submit_trek_review(id: int):
    """
    Submits or updates a trip review and optional photo.
    Allowed for verified trekkers who booked this trek, as well as staff/admin.
    """
    trek = trek_service.get_public_trek(id)
    if trek is None:
        abort(404)

    from app.services import review_service, media_service
    can_post, booking, reason = review_service.user_can_review_trek(current_user, trek)
    if not can_post and reason != "already_reviewed":
        flash("Only verified trekkers who have joined this trek can share reviews.", "warning")
        return redirect(url_for("public.trek_detail", id=trek.id))

    try:
        rating = int(request.form.get("rating", 5))
    except (ValueError, TypeError):
        rating = 5

    comment = request.form.get("comment", "").strip() or None
    photo_file = request.files.get("photo")
    image_url = None

    if photo_file and photo_file.filename:
        try:
            caption = request.form.get("photo_caption", "").strip() or f"Trip memory by {current_user.name.split()[0]}"
            media = media_service.save_upload(trek, photo_file, current_user, caption=caption)
            image_url = f"/media/{media.id}"
        except media_service.InvalidMediaError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("public.trek_detail", id=trek.id, _anchor="reviews"))

    existing = review_service.get_user_trek_review(current_user, trek)
    if existing:
        existing.rating = rating
        existing.comment = comment
        if image_url:
            existing.image_url = image_url
        db.session.commit()
        flash("Your trip experience and comments have been updated!", "success")
    else:
        review_service.create_review(
            user=current_user,
            booking=booking,
            rating=rating,
            comment=comment,
            image_url=image_url,
            trek=trek,
            allow_uncompleted=True,
        )
        db.session.commit()
        flash("Thank you! Your trip review and photo have been posted.", "success")

    return redirect(url_for("public.trek_detail", id=trek.id, _anchor="reviews"))


@public_bp.route("/treks/<hashid:id>/reviews/<uuid:review_id>/delete", methods=["POST"])
@login_required
def delete_trek_review(id: int, review_id):
    """Allows reviewers or staff/admin to delete a review."""
    from app.models.trek_review import TrekReview
    from app.services import review_service
    review = TrekReview.query.filter_by(id=review_id).first_or_404()
    if review.user_id != current_user.id and getattr(current_user, "role", "") not in ("admin", "staff"):
        abort(403)

    review_service.delete_review(review)
    db.session.commit()
    flash("Review removed.", "info")
    return redirect(url_for("public.trek_detail", id=id, _anchor="reviews"))


@public_bp.route("/treks/<hashid:id>/itinerary/pdf")
def itinerary_pdf(id: int):
    """Downloads branded itinerary PDF. 404 if pending/unapproved or no itinerary."""
    trek = trek_service.get_public_trek(id)
    if trek is None:
        abort(404)
    from app.services import itinerary_service
    days = itinerary_service.get_itinerary(trek)
    if not days:
        abort(404)
    pdf_bytes = itinerary_service.render_pdf(trek)
    from flask import Response
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=itinerary_{trek.id}.pdf"},
    )


@public_bp.route("/treks/<hashid:id>/reviews")
def trek_reviews(id: int):
    """Public paginated reviews for a completed trek."""
    trek = trek_service.get_public_trek(id)
    if trek is None:
        abort(404)
    from app.services import review_service
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 20, type=int), 100)
    pagination = review_service.list_reviews(trek, page=page, per_page=per_page)
    avg_rating, count = review_service.average_rating(trek)
    return render_template(
        "trekker/reviews_list.html",
        trek=trek,
        pagination=pagination,
        avg_rating=avg_rating,
        review_count=count,
    )


@public_bp.route("/media/<uuid:id>")
def serve_media(id):
    """Serves tenant-scoped uploaded media files safely."""
    from app.models.trek_media import TrekMedia
    from app.services import media_service
    from flask import send_file
    media = TrekMedia.query.filter_by(id=id).first_or_404()
    try:
        file_path, mimetype = media_service.open_for_serving(media)
        resp = send_file(file_path, mimetype=mimetype)
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp
    except (FileNotFoundError, PermissionError):
        abort(404)


@public_bp.route("/public/call-requests", methods=["POST"])
@limiter.limit("5/minute")
def call_request_submit():
    """Receives callback inquiries from the footer modal with honeypot spam protection."""
    # Honeypot spam check
    honeypot = request.form.get("website", "").strip()
    target_url = request.referrer if _is_safe_referrer(request.referrer) else url_for("public.index")

    if honeypot:
        # Silently absorb bot submissions
        flash("Thank you! We have received your request and will call you back shortly.", "success")
        return redirect(target_url)

    name = request.form.get("name", "").strip()
    phone_option = request.form.get("phone_option", "").strip().lower()
    phone = request.form.get("phone", "").strip()
    custom_phone = request.form.get("custom_phone", "").strip()

    # If user selected registered phone and is authenticated, ensure registered phone is used
    if (phone_option == "registered" or not phone) and current_user.is_authenticated and getattr(current_user, "mobile", None):
        phone = current_user.mobile
    elif phone_option == "custom" and custom_phone:
        phone = custom_phone

    if not name and current_user.is_authenticated:
        name = current_user.name

    preferred_time = request.form.get("preferred_time")
    message = request.form.get("message")

    try:
        call_request_service.create_call_request(
            name=name,
            phone=phone,
            preferred_time=preferred_time,
            message=message,
        )
        db.session.commit()
        flash("Thank you! We have received your request and will call you back shortly.", "success")
    except (InvalidMobileError, ValueError) as exc:
        flash(str(exc), "danger")

    return redirect(target_url)


@public_bp.route("/terms")
def terms():
    """Renders the tenant's legal terms."""
    return render_template("trekker/legal.html", kind="terms", title="Terms & Conditions")


@public_bp.route("/privacy")
def privacy():
    """Renders the tenant's privacy policy."""
    return render_template("trekker/legal.html", kind="privacy", title="Privacy Policy")


@public_bp.route("/unsubscribe/<token>", methods=["GET", "POST"])
def unsubscribe(token: str):
    """One-click email/marketing unsubscribe handler (P11)."""
    from flask import g
    from app.models.user import User
    from app.services import auth_service, notification_service

    result = notification_service.verify_unsubscribe_token(token)
    if not result:
        abort(404)
    user_id, channel = result

    user = User.query.filter_by(id=user_id).first_or_404()
    if g.organization and user.organization_id != g.organization.id:
        abort(404)

    if request.method == "POST":
        auth_service.record_consent(
            user=user,
            channel=channel,
            consent_type="marketing",
            granted=False,
            source="unsubscribe_link",
        )
        db.session.commit()
        return render_template("trekker/unsubscribe.html", channel=channel, confirmed=True, user=user)

    return render_template("trekker/unsubscribe.html", channel=channel, confirmed=False, token=token, user=user)


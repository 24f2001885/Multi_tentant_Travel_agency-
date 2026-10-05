"""
Public, anonymous-safe routes for discovery, legal documentation, and call requests (TDR v2 §5.4; PDR v2 §5.4, §5.13).
"""

from urllib.parse import urlparse
from flask import abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user

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


@public_bp.route("/")
def index():
    """Tenant landing page for anonymous visitors, or dashboard redirect for authenticated users."""
    # print(f"[DEBUG] public route {request.path} org={getattr(g, 'organization_id', None)}")
    if current_user.is_authenticated:
        return redirect(_role_dashboard_url(current_user.role))

    featured_treks = trek_service.list_open_treks()[:6]
    return render_template("trekker/landing.html", featured_treks=featured_treks)


@public_bp.route("/treks")
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
    return render_template("trekker/trek_detail.html", trek=trek)


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

    name = request.form.get("name", "")
    phone = request.form.get("phone", "")
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

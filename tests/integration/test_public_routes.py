"""
Integration tests for app/public/routes.py and account completion gate (TDR v2 §5.4, §5.13; Milestone P7).
Zero external network calls; fully offline.
"""

from datetime import datetime, timezone
import pytest
from app.extensions import db
from app.models.call_request import CallRequest
from app.models.trek import Trek
from app.services import id_codec
from app.tenancy.scoping import tenant_bypass, tenant_context


def test_anonymous_public_views_render(client_a, org_a, make_trek):
    """Anonymous visitors can access landing, browse, and open trek detail."""
    trek = make_trek(org_a, name="Kashmir Great Lakes", status="open")
    token = id_codec.encode(trek.id)

    # 1. Landing page
    resp_landing = client_a.get("/")
    # print(f"[DEBUG] public test path=/ status={resp_landing.status_code}")
    assert resp_landing.status_code == 200
    assert "Kashmir Great Lakes" in resp_landing.get_data(as_text=True)

    # 2. Browse treks
    resp_browse = client_a.get("/treks")
    assert resp_browse.status_code == 200
    assert "Kashmir Great Lakes" in resp_browse.get_data(as_text=True)

    # 3. Trek detail
    resp_detail = client_a.get(f"/treks/{token}")
    assert resp_detail.status_code == 200
    assert "Kashmir Great Lakes" in resp_detail.get_data(as_text=True)


def test_pending_and_cross_tenant_treks_return_404(client_a, org_a, org_b, make_trek):
    """Pending treks and other-tenant treks return 404 Not Found on public detail route."""
    pending_trek = make_trek(org_a, name="Draft Trek", status="pending")
    token_pending = id_codec.encode(pending_trek.id)

    other_tenant_trek = make_trek(org_b, name="Org B Mountain", status="open")
    token_other = id_codec.encode(other_tenant_trek.id)

    # Pending trek on same tenant -> 404
    assert client_a.get(f"/treks/{token_pending}").status_code == 404

    # Open trek on other tenant -> 404
    assert client_a.get(f"/treks/{token_other}").status_code == 404


def test_call_request_submission_and_honeypot(client_a, org_a):
    """Valid call request submission succeeds; honeypot bot submissions are silently ignored."""
    # 1. Valid submission
    resp = client_a.post(
        "/public/call-requests",
        data={
            "name": "Jane Trekker",
            "phone": "+919876543210",
            "preferred_time": "Evening",
            "message": "Interested in private group",
            "website": "",  # Empty honeypot
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with tenant_context(org_a.id):
        cr = CallRequest.query.filter_by(phone="+919876543210").first()
        assert cr is not None
        assert cr.name == "Jane Trekker"

    # 2. Honeypot filled by bot -> silently ignored (no DB row created)
    resp_bot = client_a.post(
        "/public/call-requests",
        data={
            "name": "Spam Bot",
            "phone": "+919876543211",
            "website": "http://spamsite.com",  # Honeypot triggered
        },
        follow_redirects=True,
    )
    assert resp_bot.status_code == 200

    with tenant_context(org_a.id):
        bot_cr = CallRequest.query.filter_by(phone="+919876543211").first()
        assert bot_cr is None


def test_legal_documentation_routes(client_a):
    """Terms and privacy policy pages render with default fallback text."""
    resp_terms = client_a.get("/terms")
    assert resp_terms.status_code == 200
    assert "Terms" in resp_terms.get_data(as_text=True)

    resp_privacy = client_a.get("/privacy")
    assert resp_privacy.status_code == 200
    assert "Privacy Policy" in resp_privacy.get_data(as_text=True)


def test_account_completion_gate_intercepts_legacy_user(client_a, org_a, make_user):
    """A legacy user without mobile number or accepted terms is gated to /complete-account."""
    legacy_user = make_user(
        org_a,
        role="trekker",
        email="legacy_trekker@example.com",
        password="Password123!",
        mobile_number="UNSET",
        terms_accepted=False,
    )

    # Login
    login_resp = client_a.post(
        "/api/auth/login",
        json={"email": "legacy_trekker@example.com", "password": "Password123!"},
    )
    assert login_resp.status_code == 200

    # Attempt to browse protected area -> intercepted
    resp_dashboard = client_a.get("/trekker/dashboard")
    assert resp_dashboard.status_code == 302
    assert "/complete-account" in resp_dashboard.headers.get("Location", "")

    # Complete account
    post_complete = client_a.post(
        "/complete-account",
        data={
            "mobile_country_code": "+91",
            "mobile_number": "9876543210",
            "accept_terms": "y",
        },
        follow_redirects=False,
    )
    assert post_complete.status_code == 302

    # Gate is now cleared; protected route succeeds
    resp_after = client_a.get("/trekker/dashboard")
    assert resp_after.status_code == 200


def test_call_request_logged_in_one_click_registered_phone(client_a, org_a, make_user):
    """Logged in user can submit a callback request using their registered mobile in one click."""
    user = make_user(
        org_a,
        role="trekker",
        email="callback_user@example.com",
        password="Password123!",
        mobile_country_code="+91",
        mobile_number="9123456780",
        terms_accepted=True,
    )

    # Login user
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["organization_id"] = str(org_a.id)

    # 1. Submit with phone_option="registered" (1-click, phone field omitted or blank)
    resp = client_a.post(
        "/public/call-requests",
        data={
            "phone_option": "registered",
            "phone": "",
            "preferred_time": "Morning (9 AM - 12 PM)",
            "message": "Please call my registered number",
            "website": "",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with tenant_context(org_a.id):
        cr = CallRequest.query.filter_by(phone="+919123456780").first()
        assert cr is not None
        assert cr.name == user.name
        assert cr.preferred_time == "Morning (9 AM - 12 PM)"

    # 2. Submit with phone_option="custom"
    resp_custom = client_a.post(
        "/public/call-requests",
        data={
            "phone_option": "custom",
            "custom_phone": "+919876500000",
            "phone": "+919876500000",
            "preferred_time": "Any time / ASAP",
            "message": "Call alternate number",
            "website": "",
        },
        follow_redirects=True,
    )
    assert resp_custom.status_code == 200

    with tenant_context(org_a.id):
        cr_custom = CallRequest.query.filter_by(phone="+919876500000").first()
        assert cr_custom is not None
        assert cr_custom.name == user.name



def test_footer_and_cookie_banner_present_on_public_pages(client_a):
    """Footer and cookie consent banner are rendered on public pages."""
    resp = client_a.get("/")
    html = resp.get_data(as_text=True)
    assert 'id="tma-cookie-banner"' in html
    assert 'id="requestCallModal"' in html

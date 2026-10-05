"""
Integration tests for app/tenancy/resolver.py (TDR v2 §2, Milestone P7).
Tests HTTP Host resolution, domain routability, exempt paths, and cross-host session isolation.
"""

from datetime import datetime, timezone
import pytest
from app.extensions import db
from app.models import Domain, Organization
from app.tenancy.scoping import tenant_bypass


def test_unknown_host_returns_404(app):
    """An unregistered or unmapped Host header returns 404 Not Found."""
    client = app.test_client()
    resp = client.get("/", headers={"Host": "unknown-ghost-host.localhost"})
    # print(f"[DEBUG] resolver test host=unknown status={resp.status_code}")
    assert resp.status_code == 404


def test_unverified_custom_domain_returns_404(app, org_a):
    """A custom domain that has not been verified (verified_at is None) returns 404."""
    with app.app_context(), tenant_bypass():
        custom_dom = Domain(
            organization_id=org_a.id,
            domain_name="unverified.customtrek.com",
            domain_type="custom",
            is_primary=False,
            verified_at=None,
        )
        db.session.add(custom_dom)
        db.session.commit()

    client = app.test_client()
    resp = client.get("/", headers={"Host": "unverified.customtrek.com"})
    assert resp.status_code == 404


def test_verified_custom_domain_resolves_tenant(app, org_a):
    """A verified custom domain resolves correctly and renders the tenant landing page."""
    with app.app_context(), tenant_bypass():
        custom_dom = Domain(
            organization_id=org_a.id,
            domain_name="verified.customtrek.com",
            domain_type="custom",
            is_primary=False,
            verified_at=datetime.now(timezone.utc),
        )
        db.session.add(custom_dom)
        db.session.commit()

    client = app.test_client()
    resp = client.get("/", headers={"Host": "verified.customtrek.com"})
    assert resp.status_code == 200


def test_suspended_organization_returns_403(app, org_a):
    """Requests to an organization with status='suspended' return 403 Forbidden."""
    with app.app_context(), tenant_bypass():
        org = Organization.query.get(org_a.id)
        org.status = "suspended"
        db.session.commit()

    client = app.test_client()
    resp = client.get("/", headers={"Host": "a.localhost"})
    assert resp.status_code == 403


def test_port_stripping_resolves_tenant(client_a):
    """Port numbers in the Host header (e.g. a.localhost:5000) are cleanly stripped."""
    resp = client_a.get("/", headers={"Host": "a.localhost:5000"})
    assert resp.status_code == 200


def test_platform_admin_host_skips_tenancy(app):
    """Requests to PLATFORM_ADMIN_HOST bypass tenant resolution without 404."""
    client = app.test_client()
    resp = client.get("/api/auth/login", headers={"Host": "platform.localhost"})
    # Resolver allows request through; route handles method or payload
    assert resp.status_code != 404


def test_webhook_path_exempt_from_tenant_resolution(app):
    """Exempt webhook paths (e.g. /api/payments/webhook) bypass tenant resolution."""
    client = app.test_client()
    # Sending invalid signature directly tests that resolver did not 404 on unknown host
    resp = client.post(
        "/api/payments/webhook",
        headers={"Host": "gateway-callback.external.net"},
        data=b"{}",
    )
    # Signature check returns 400, proving resolver let the request reach the webhook resource
    assert resp.status_code == 400


def test_cross_host_session_cookie_rejected(app, client_a, client_b, org_a, org_b, make_user):
    """A session cookie issued for Tenant A is rejected when submitted to Tenant B."""
    user_a = make_user(org_a, role="trekker", email="trekker_a@example.com", password="Password123!")

    # Login on Tenant A
    login_resp = client_a.post(
        "/api/auth/login",
        json={"email": "trekker_a@example.com", "password": "Password123!"},
        headers={"Host": "a.localhost"},
    )
    assert login_resp.status_code == 200

    # Protected endpoint on Tenant A succeeds
    resp_a = client_a.get("/tickets", headers={"Host": "a.localhost"})
    assert resp_a.status_code == 200

    # Extract session cookie from Tenant A response
    cookie_header = login_resp.headers.get("Set-Cookie", "")

    # Protected endpoint on Tenant B must reject session (redirect to login)
    resp_b = client_b.get("/tickets", headers={"Host": "b.localhost", "Cookie": cookie_header})
    # Login required redirect to /login
    assert resp_b.status_code == 302
    assert "/login" in resp_b.headers.get("Location", "")

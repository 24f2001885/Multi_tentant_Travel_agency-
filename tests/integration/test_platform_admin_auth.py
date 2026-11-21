"""
Integration tests for Platform Admin authentication, session isolation, and host boundary enforcement (TDR v2 §2, §12, §13; PDR v2 §14.1; Milestone P8).
"""

import pytest
from app.extensions import db
from app.models.organization import Organization
from app.models.platform_admin import PlatformAdmin
from app.models.user import User
from app.services import tenancy_service, rbac_service
from app.tenancy.scoping import tenant_bypass


def test_platform_login_generic_failure(client_platform, platform_admin):
    """Platform login returns a generic error for wrong email or wrong password, preventing enumeration."""
    # 1. Non-existent email
    resp1 = client_platform.post(
        "/platform/login",
        data={"email": "nonexistent@platform.local", "password": "any-password"},
        follow_redirects=True,
    )
    assert resp1.status_code == 200
    assert b"Invalid email or password" in resp1.data

    # 2. Existing email, wrong password
    resp2 = client_platform.post(
        "/platform/login",
        data={"email": platform_admin.email, "password": "wrong-password"},
        follow_redirects=True,
    )
    assert resp2.status_code == 200
    assert b"Invalid email or password" in resp2.data


def test_platform_login_rate_limiting(client_platform):
    """Platform login enforces 5 attempts per minute rate limit."""
    # Attempt 5 times
    for i in range(5):
        resp = client_platform.post(
            "/platform/login",
            data={"email": f"rate{i}@platform.local", "password": "bad"},
        )
        assert resp.status_code in (200, 302)

    # 6th attempt should be rate limited (429)
    resp_limit = client_platform.post(
        "/platform/login",
        data={"email": "rate_limit@platform.local", "password": "bad"},
    )
    assert resp_limit.status_code == 429


def test_platform_login_success_and_logout(client_platform, platform_admin):
    """Platform admin logs in successfully, accesses dashboard, and logs out."""
    resp = client_platform.post(
        "/platform/login",
        data={"email": platform_admin.email, "password": "change-me-platform-password"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Platform Console" in resp.data
    assert platform_admin.name.encode() in resp.data

    # Logout
    resp_out = client_platform.post("/platform/logout", follow_redirects=True)
    assert resp_out.status_code == 200
    assert b"You have been signed out of the Platform Console." in resp_out.data


def test_session_isolation_tenant_user_cannot_access_platform(client_platform, org_a, make_user):
    """A tenant user session cannot access platform admin endpoints."""
    admin_user = make_user(org_a, role="admin", email="tenant_admin@a.local")

    with client_platform.session_transaction() as sess:
        sess["_user_id"] = str(admin_user.id)

    resp = client_platform.get("/platform/dashboard", follow_redirects=False)
    # Redirected to platform login
    assert resp.status_code == 302
    assert "/platform/login" in resp.location


def test_session_isolation_platform_admin_cannot_access_tenant(client_a, platform_admin):
    """A platform admin session cannot access tenant authenticated routes."""
    with client_a.session_transaction() as sess:
        sess["platform_admin_id"] = str(platform_admin.id)

    resp = client_a.get("/admin/dashboard", follow_redirects=False)
    # Redirected to tenant login because current_user is anonymous
    assert resp.status_code == 302
    assert "/login" in resp.location


def test_host_boundary_platform_host_blocks_tenant_routes(client_platform):
    """Requests to PLATFORM_ADMIN_HOST for non-platform routes return 404."""
    resp = client_platform.get("/treks")
    assert resp.status_code == 404

    resp_auth = client_platform.get("/login")
    assert resp_auth.status_code == 404


def test_host_boundary_tenant_host_blocks_platform_routes(client_a):
    """Requests to tenant hosts for /platform/* routes return 404."""
    resp = client_a.get("/platform/login")
    assert resp.status_code == 404

    resp_dash = client_a.get("/platform/dashboard")
    assert resp_dash.status_code == 404


def test_suspension_enforcement(client_a, org_a):
    """When an organization is suspended, tenant endpoints return 403."""
    with tenant_bypass():
        org = db.session.get(Organization, org_a.id)
        org.status = "suspended"
        db.session.commit()

    resp = client_a.get("/")
    assert resp.status_code == 403

    resp_login = client_a.get("/login")
    assert resp_login.status_code == 403


def test_onboard_organization_displays_temp_password(client_platform, platform_admin):
    """Onboarding a tenant renders the generated temporary password on the response page."""
    # Login as platform admin
    login_resp = client_platform.post(
        "/platform/login",
        data={"email": platform_admin.email, "password": "change-me-platform-password"},
    )
    assert login_resp.status_code == 302

    with tenant_bypass():
        rbac_service.seed_catalog()

    resp = client_platform.post(
        "/platform/organizations/new",
        data={
            "name": "K2 Expeditions",
            "subdomain": "k2trek",
            "tenant_admin_name": "K2 Leader",
            "tenant_admin_email": "leader@k2.local",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Tenant Provisioned Successfully" in resp.data
    assert b"One-Time Temporary Password" in resp.data
    assert b"k2trek.localhost" in resp.data

    # Verify tenant admin was created
    with tenant_bypass():
        user = User.query.filter_by(email="leader@k2.local").first()
        assert user is not None
        assert user.must_reset_password is True


def test_update_organization_payout_settings(client_platform, platform_admin, org_a):
    """Platform operator can configure Razorpay Route account ID and GST number from the UI."""
    login_resp = client_platform.post(
        "/platform/login",
        data={"email": platform_admin.email, "password": "change-me-platform-password"},
    )
    assert login_resp.status_code == 302

    resp = client_platform.post(
        f"/platform/organizations/{org_a.id}/payout",
        data={
            "razorpay_route_account_id": "acc_TestRazorpay123",
            "gst_number": "07AAAAA0000A1Z5",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"acc_TestRazorpay123" in resp.data

    with tenant_bypass():
        reloaded_org = Organization.query.get(org_a.id)
        assert reloaded_org.razorpay_route_account_id == "acc_TestRazorpay123"
        assert reloaded_org.gst_number == "07AAAAA0000A1Z5"

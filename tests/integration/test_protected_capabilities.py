"""
Integration tests for protected capability enforcement and role management boundaries
(TDR v2 §6, §10; PDR v2 §5.3, §11, §14.1; Milestone P8).
"""

import pytest
from app.extensions import db
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from app.services import rbac_service
from app.tenancy.scoping import tenant_bypass, tenant_context


def test_tenant_owner_can_access_roles_management(client_a, org_a, make_user):
    """Tenant owner (user.id == org.tenant_admin_user_id) accesses role management successfully."""
    with tenant_bypass():
        rbac_service.seed_catalog()
        owner = make_user(org_a, role="admin", email="owner@a.local")
        org = db.session.get(Organization, org_a.id)
        org.tenant_admin_user_id = owner.id
        db.session.commit()

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(owner.id)

    resp = client_a.get("/admin/roles")
    assert resp.status_code == 200
    assert b"Roles &amp; Capabilities" in resp.data or b"Roles & Capabilities" in resp.data


def test_non_owner_admin_gets_403_on_protected_endpoints(client_a, org_a, make_user):
    """Non-owner admin (role=admin but user.id != org.tenant_admin_user_id) gets 403 on protected routes."""
    with tenant_bypass():
        rbac_service.seed_catalog()
        owner = make_user(org_a, role="admin", email="owner@a.local")
        non_owner = make_user(org_a, role="admin", email="sub_admin@a.local")
        org = db.session.get(Organization, org_a.id)
        org.tenant_admin_user_id = owner.id
        db.session.commit()

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(non_owner.id)

    # Attempt to list roles
    resp_list = client_a.get("/admin/roles")
    assert resp_list.status_code == 403

    # Attempt to access role creation
    resp_new_get = client_a.get("/admin/roles/new")
    assert resp_new_get.status_code == 403

    resp_new_post = client_a.post("/admin/roles/new", data={"name": "Hacker Role", "capabilities": ["tickets.view"]})
    assert resp_new_post.status_code == 403


def test_staff_user_gets_403_on_protected_endpoints(client_a, org_a, make_user):
    """Staff member gets 403 when hitting protected role management endpoints."""
    with tenant_bypass():
        rbac_service.seed_catalog()
        owner = make_user(org_a, role="admin", email="owner@a.local")
        staff = make_user(org_a, role="staff", email="staff@a.local")
        org = db.session.get(Organization, org_a.id)
        org.tenant_admin_user_id = owner.id
        db.session.commit()

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(staff.id)

    resp = client_a.get("/admin/roles")
    assert resp.status_code == 403


def test_smuggled_protected_capability_key_returns_422(client_a, org_a, make_user):
    """Attempting to create a role with a protected capability via form submission fails with 422."""
    with tenant_bypass():
        rbac_service.seed_catalog()
        owner = make_user(org_a, role="admin", email="owner@a.local")
        org = db.session.get(Organization, org_a.id)
        org.tenant_admin_user_id = owner.id
        db.session.commit()

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(owner.id)

    resp = client_a.post(
        "/admin/roles/new",
        data={
            "name": "Smuggler Role",
            "capabilities": ["treks.manage_assigned", "settings.org.edit"],
        },
    )
    assert resp.status_code == 422

    # Verify role was not created in database
    with tenant_context(org_a.id):
        assert Role.query.filter_by(name="Smuggler Role").first() is None


def test_smuggled_protected_capability_on_edit_returns_422(client_a, org_a, make_user):
    """Attempting to update an existing role with a protected capability via form submission returns 422."""
    with tenant_bypass():
        rbac_service.seed_catalog()
        owner = make_user(org_a, role="admin", email="owner@a.local")
        org = db.session.get(Organization, org_a.id)
        org.tenant_admin_user_id = owner.id
        db.session.commit()

        # Create valid role first
        role = rbac_service.create_role(owner, org, "Legit Role", ["tickets.view"])
        db.session.commit()
        role_id = role.id

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(owner.id)

    resp = client_a.post(
        f"/admin/roles/{role_id}/edit",
        data={
            "name": "Legit Role Modified",
            "capabilities": ["tickets.view", "manual_booking.toggle"],
        },
    )
    assert resp.status_code == 422

    # Verify role retains original capabilities
    with tenant_context(org_a.id):
        refreshed = db.session.get(Role, role_id)
        cap_keys = {c.key for c in refreshed.capabilities}
        assert "manual_booking.toggle" not in cap_keys
        assert "tickets.view" in cap_keys


def test_role_builder_does_not_render_protected_capabilities(client_a, org_a, make_user):
    """The role builder form template excludes protected capabilities from choices."""
    with tenant_bypass():
        rbac_service.seed_catalog()
        owner = make_user(org_a, role="admin", email="owner@a.local")
        org = db.session.get(Organization, org_a.id)
        org.tenant_admin_user_id = owner.id
        db.session.commit()

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(owner.id)

    resp = client_a.get("/admin/roles/new")
    assert resp.status_code == 200

    # Non-protected capabilities should be present
    assert b"treks.manage_assigned" in resp.data
    assert b"tickets.view" in resp.data

    # Protected capabilities must NOT be present as options
    assert b"settings.org.edit" not in resp.data
    assert b"manual_booking.toggle" not in resp.data
    assert b"payments.payout.configure" not in resp.data
    assert b"roles.manage" not in resp.data


def test_staff_with_smuggled_session_capability_still_cannot_access_protected_endpoint(client_a, org_a, make_user):
    """Even if a staff user has protected keys injected into session['capability_keys'], protected endpoints remain blocked."""
    with tenant_bypass():
        rbac_service.seed_catalog()
        owner = make_user(org_a, role="admin", email="owner@a.local")
        staff = make_user(org_a, role="staff", email="staff@a.local")
        org = db.session.get(Organization, org_a.id)
        org.tenant_admin_user_id = owner.id
        db.session.commit()

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(staff.id)
        # Smuggle protected capabilities into session
        sess["capability_keys"] = ["roles.manage", "settings.org.edit"]

    resp = client_a.get("/admin/roles")
    assert resp.status_code == 403

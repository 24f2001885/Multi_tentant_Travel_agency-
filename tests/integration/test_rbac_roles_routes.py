"""
Integration tests for RBAC role management routes, staff assignment, snapshot-at-login,
and capability-driven navigation (TDR v2 §6, §10; PDR v2 §5.3, §11, §14.1; Milestone P8).
"""

import pytest
from app.extensions import db
from app.models.organization import Organization
from app.models.role import Role
from app.models.staff_profile import StaffProfile
from app.models.user import User
from app.services import rbac_service
from app.services.id_codec import encode
from app.tenancy.scoping import tenant_bypass, tenant_context


def test_role_crud_http_workflow(client_a, org_a, make_user):
    """Owner admin can create, list, edit, and delete custom roles via HTTP."""
    with tenant_bypass():
        rbac_service.seed_catalog()
        owner = make_user(org_a, role="admin", email="owner_crud@a.example.com")
        org = db.session.get(Organization, org_a.id)
        org.tenant_admin_user_id = owner.id
        db.session.commit()

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(owner.id)

    # 1. GET /admin/roles
    resp_list = client_a.get("/admin/roles")
    assert resp_list.status_code == 200

    # 2. POST /admin/roles/new
    resp_create = client_a.post(
        "/admin/roles/new",
        data={
            "name": "Support Specialist",
            "capabilities": ["tickets.view", "tickets.respond"],
        },
        follow_redirects=True,
    )
    assert resp_create.status_code == 200
    assert b"Role created successfully" in resp_create.data
    assert b"Support Specialist" in resp_create.data

    with tenant_context(org_a.id):
        role = Role.query.filter_by(name="Support Specialist").first()
        assert role is not None
        role_id = role.id
        cap_keys = {c.key for c in role.capabilities}
        assert cap_keys == {"tickets.view", "tickets.respond"}

    # 3. POST /admin/roles/<id>/edit
    resp_edit = client_a.post(
        f"/admin/roles/{role_id}/edit",
        data={
            "name": "Senior Support Specialist",
            "capabilities": ["tickets.view"],
        },
        follow_redirects=True,
    )
    assert resp_edit.status_code == 200
    assert b"Role updated successfully" in resp_edit.data
    assert b"Senior Support Specialist" in resp_edit.data

    with tenant_context(org_a.id):
        updated_role = db.session.get(Role, role_id)
        assert updated_role.name == "Senior Support Specialist"
        assert {c.key for c in updated_role.capabilities} == {"tickets.view"}

    # 4. POST /admin/roles/<id>/delete
    resp_delete = client_a.post(f"/admin/roles/{role_id}/delete", follow_redirects=True)
    assert resp_delete.status_code == 200
    assert b"was successfully deleted" in resp_delete.data

    with tenant_context(org_a.id):
        assert db.session.get(Role, role_id) is None


def test_delete_role_in_use_rejected(client_a, org_a, make_user):
    """Deleting a role currently assigned to a staff profile is rejected."""
    with tenant_bypass():
        rbac_service.seed_catalog()
        owner = make_user(org_a, role="admin", email="owner_del@a.example.com")
        staff = make_user(org_a, role="staff", email="staff_del@a.example.com")
        org = db.session.get(Organization, org_a.id)
        org.tenant_admin_user_id = owner.id

        role = rbac_service.create_role(owner, org, "In-Use Role", ["tickets.view"])
        db.session.flush()

        profile = StaffProfile(
            organization_id=org_a.id,
            user_id=staff.id,
            role_id=role.id,
            approval_status="approved",
        )
        db.session.add(profile)
        db.session.commit()
        role_id = role.id

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(owner.id)

    resp = client_a.post(f"/admin/roles/{role_id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Cannot delete role while staff members are assigned to it" in resp.data

    with tenant_context(org_a.id):
        assert db.session.get(Role, role_id) is not None


def test_assign_role_to_staff_profile(client_a, org_a, make_user):
    """Owner admin can assign a role to an approved staff profile."""
    with tenant_bypass():
        rbac_service.seed_catalog()
        owner = make_user(org_a, role="admin", email="owner_assign@a.example.com")
        staff = make_user(org_a, role="staff", email="staff_assign@a.example.com")
        org = db.session.get(Organization, org_a.id)
        org.tenant_admin_user_id = owner.id

        role = rbac_service.create_role(owner, org, "Operations Lead", ["tickets.view"])
        db.session.flush()

        profile = StaffProfile(
            organization_id=org_a.id,
            user_id=staff.id,
            role_id=None,
            approval_status="approved",
        )
        db.session.add(profile)
        db.session.commit()

        profile_hashid = encode(profile.id)
        role_id_str = str(role.id)

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(owner.id)

    resp = client_a.post(
        f"/admin/staff/{profile_hashid}/role",
        data={"role_id": role_id_str},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"assigned to" in resp.data

    with tenant_context(org_a.id):
        refreshed_prof = db.session.get(StaffProfile, profile.id)
        assert refreshed_prof.role_id == role.id


def test_snapshot_at_login_behavior(client_a, org_a, make_user, make_trek):
    """Staff capabilities snapshot into session at login and persist until re-login (PDR v2 §5.3)."""
    with tenant_bypass():
        rbac_service.seed_catalog()
        owner = make_user(org_a, role="admin", email="owner_snap@a.example.com")
        staff = make_user(org_a, role="staff", email="staff_snap@a.example.com", password="StaffPassword123!")
        org = db.session.get(Organization, org_a.id)
        org.tenant_admin_user_id = owner.id

        trek = make_trek(org_a, name="Hampta Pass")
        trek_hashid = encode(trek.id)

        role = rbac_service.create_role(owner, org, "Trek Support", ["tickets.view"])
        db.session.flush()

        profile = StaffProfile(
            organization_id=org_a.id,
            user_id=staff.id,
            role_id=role.id,
            approval_status="approved",
        )
        db.session.add(profile)
        db.session.flush()

        from app.models.trek import Trek
        trek_obj = db.session.get(Trek, trek.id)
        trek_obj.assigned_staff_id = profile.id
        db.session.commit()
        role_id = role.id

    # 1. Staff logs in via form
    login_resp = client_a.post(
        "/login",
        data={"email": "staff_snap@a.example.com", "password": "StaffPassword123!"},
        follow_redirects=True,
    )
    assert login_resp.status_code == 200

    # 2. Staff accesses tickets list (tickets.view allowed)
    tickets_resp = client_a.get("/tickets")
    assert tickets_resp.status_code == 200

    # Staff accesses participants (participants.view NOT allowed) -> 403
    part_resp = client_a.get(f"/staff/treks/{trek_hashid}/participants")
    assert part_resp.status_code == 403

    # 3. Admin updates role in DB: adds participants.view
    with tenant_bypass():
        updated_role = db.session.get(Role, role_id)
        rbac_service.update_role(
            actor=owner,
            org=org,
            role=updated_role,
            name="Trek Support",
            capability_keys=["tickets.view", "participants.view"],
        )
        db.session.commit()

    # 4. Staff makes a request without re-logging:
    # Snapshot in session still lacks participants.view -> STILL 403!
    part_resp2 = client_a.get(f"/staff/treks/{trek_hashid}/participants")
    assert part_resp2.status_code == 403

    # 5. Staff logs out and logs back in
    client_a.post("/logout", follow_redirects=True)
    relogin_resp = client_a.post(
        "/login",
        data={"email": "staff_snap@a.example.com", "password": "StaffPassword123!"},
        follow_redirects=True,
    )
    assert relogin_resp.status_code == 200

    # 6. Now snapshot contains participants.view!
    # Request to participants route succeeds (200)
    part_resp3 = client_a.get(f"/staff/treks/{trek_hashid}/participants")
    assert part_resp3.status_code == 200


def test_capability_driven_navigation_rendering(client_a, org_a, make_user):
    """Sidebars and navigation dynamically render elements based on session capabilities."""
    with tenant_bypass():
        rbac_service.seed_catalog()
        owner = make_user(org_a, role="admin", email="owner_nav@a.example.com")
        staff1 = make_user(org_a, role="staff", email="staff_tickets@a.example.com", password="Pass1234!")
        staff2 = make_user(org_a, role="staff", email="staff_treks@a.example.com", password="Pass1234!")
        org = db.session.get(Organization, org_a.id)
        org.tenant_admin_user_id = owner.id

        role_tickets = rbac_service.create_role(owner, org, "Ticket Only", ["tickets.view"])
        role_treks = rbac_service.create_role(owner, org, "Treks Only", ["treks.manage_assigned"])
        db.session.flush()

        p1 = StaffProfile(organization_id=org_a.id, user_id=staff1.id, role_id=role_tickets.id, approval_status="approved")
        p2 = StaffProfile(organization_id=org_a.id, user_id=staff2.id, role_id=role_treks.id, approval_status="approved")
        db.session.add_all([p1, p2])
        db.session.commit()

    # Staff 1 (tickets only)
    resp_l1 = client_a.post("/login", data={"email": "staff_tickets@a.example.com", "password": "Pass1234!"}, follow_redirects=True)
    assert resp_l1.status_code == 200
    resp_s1 = client_a.get("/staff/dashboard")
    assert resp_s1.status_code == 200
    assert b"Tickets" in resp_s1.data
    assert b"Assigned Treks" not in resp_s1.data

    # Staff 2 (treks only)
    client_a.post("/logout", follow_redirects=True)
    resp_l2 = client_a.post("/login", data={"email": "staff_treks@a.example.com", "password": "Pass1234!"}, follow_redirects=True)
    assert resp_l2.status_code == 200
    resp_s2 = client_a.get("/staff/dashboard")
    assert resp_s2.status_code == 200
    assert b"Assigned Treks" in resp_s2.data
    assert b'href="/tickets"' not in resp_s2.data

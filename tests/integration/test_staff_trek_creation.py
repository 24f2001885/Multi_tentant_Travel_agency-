"""
Integration tests for the staff-created-trek flow (Milestone P10).
Covers capability gating, pending/unassigned landing, creator content editing,
admin assignment & approval, and smuggled field protection.
"""

from datetime import date, timedelta
import pytest

from app.extensions import db
from app.models.trek import Trek
from app.models.staff_profile import StaffProfile
from app.services import id_codec
from app.tenancy.scoping import tenant_context


def test_staff_trek_creation_capability_gate(app, org_a, make_user, client_a):
    """Staff without treks.create capability gets 403 on trek creation route."""
    with app.app_context(), tenant_context(org_a.id):
        staff = make_user(org_a, role="staff", email="staff_nocap@example.com")
        profile = StaffProfile(
            organization_id=org_a.id,
            user_id=staff.id,
            approval_status="approved",
        )
        db.session.add(profile)
        db.session.commit()
        staff_id = staff.id

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(staff_id)
        sess["capability_keys"] = []  # No treks.create

    resp_get = client_a.get("/staff/treks/new")
    assert resp_get.status_code == 403

    resp_post = client_a.post("/staff/treks/new", data={"title": "Unauthorized Trek"})
    assert resp_post.status_code == 403


def test_staff_trek_creation_flow_and_assignment(app, org_a, make_user, client_a):
    """
    Staff creates trek -> lands pending & unassigned -> creator can edit content
    -> admin assigns staff -> assignee gains manage access.
    """
    with app.app_context(), tenant_context(org_a.id):
        # Admin owner
        admin = make_user(org_a, role="admin", email="admin@example.com")
        org_a.tenant_admin_user_id = admin.id

        # Staff Creator
        staff_creator = make_user(org_a, role="staff", email="creator@example.com")
        profile_creator = StaffProfile(
            organization_id=org_a.id,
            user_id=staff_creator.id,
            approval_status="approved",
        )
        db.session.add(profile_creator)

        # Staff Assignee
        staff_assignee = make_user(org_a, role="staff", email="assignee@example.com")
        profile_assignee = StaffProfile(
            organization_id=org_a.id,
            user_id=staff_assignee.id,
            approval_status="approved",
        )
        db.session.add(profile_assignee)
        db.session.commit()

        creator_id = staff_creator.id
        assignee_id = staff_assignee.id
        assignee_profile_id = profile_assignee.id
        admin_id = admin.id

    # 1. Staff Creator submits new trek with attempted smuggled status & assigned_staff_id
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(creator_id)
        sess["capability_keys"] = ["treks.create", "treks.edit_content"]

    start = date.today() + timedelta(days=20)
    end = date.today() + timedelta(days=25)
    resp_create = client_a.post(
        "/staff/treks/new",
        data={
            "title": "Kedarkantha Winter Trek",
            "location": "Uttarakhand",
            "difficulty": "easy",
            "duration_days": 5,
            "total_slots": 12,
            "price": 6000.0,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "status": "open",  # Smuggled status attempt
            "assigned_staff_id": assignee_profile_id,  # Smuggled assigned_staff_id attempt
        },
        follow_redirects=True,
    )
    assert resp_create.status_code == 200

    with app.app_context(), tenant_context(org_a.id):
        trek = Trek.query.filter_by(name="Kedarkantha Winter Trek").first()
        assert trek is not None
        assert trek.created_by_user_id == creator_id
        # Integrity checks: Must land pending and unassigned despite smuggled form data
        assert trek.status == "pending"
        assert trek.assigned_staff_id is None
        trek_id = trek.id

    trek_hash = id_codec.encode(trek_id)

    # 2. Staff Creator can edit content (add day to itinerary)
    resp_add_day = client_a.post(
        f"/staff/treks/{trek_hash}/content/itinerary/days/add",
        data={"title": "Day 1: Base camp arrival", "description": "Reach Sankri village"},
        follow_redirects=True,
    )
    assert resp_add_day.status_code == 200

    with app.app_context(), tenant_context(org_a.id):
        trek = Trek.query.get(trek_id)
        assert len(trek.itinerary_days) == 1
        assert trek.itinerary_days[0].title == "Day 1: Base camp arrival"

    # Staff Creator cannot access admin edit page for core fields
    resp_admin_edit = client_a.get(f"/admin/treks/{trek_hash}/edit")
    assert resp_admin_edit.status_code in (403, 302)

    # 3. Admin views "Awaiting assignment" list
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
        sess["capability_keys"] = []

    resp_admin_list = client_a.get("/admin/treks?awaiting_assignment=1")
    assert resp_admin_list.status_code == 200
    assert "Kedarkantha Winter Trek" in resp_admin_list.data.decode("utf-8")

    # 4. Admin assigns Staff Assignee and transitions status
    with app.app_context(), tenant_context(org_a.id):
        t = Trek.query.get(trek_id)
        t.assigned_staff_id = assignee_profile_id
        t.status = "open"
        db.session.commit()

    # 5. Assignee now has manage access
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(assignee_id)
        sess["capability_keys"] = ["treks.manage_assigned", "treks.edit_content"]

    resp_manage = client_a.get(f"/staff/treks/{trek_hash}/manage")
    assert resp_manage.status_code == 200
    assert "Kedarkantha Winter Trek" in resp_manage.data.decode("utf-8")

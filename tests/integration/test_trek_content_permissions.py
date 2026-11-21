"""
Integration tests for the trek content edit permission matrix (Milestone P10).
Tests Tenant Admin, staff creator, staff assignee, unrelated staff,
staff without treks.edit_content, trekker, and anonymous against itinerary/media routes.
"""

from datetime import date, timedelta
import pytest

from app.extensions import db
from app.models.trek import Trek
from app.models.staff_profile import StaffProfile
from app.services import id_codec
from app.tenancy.scoping import tenant_context


def test_trek_content_permission_matrix(app, org_a, make_user, client_a):
    """
    Verifies that ONLY Tenant Admin, the creator staff, or the assigned staff
    (with treks.edit_content) can edit trek content.
    """
    with app.app_context(), tenant_context(org_a.id):
        # 1. Tenant Admin
        admin = make_user(org_a, role="admin", email="admin_matrix@example.com")
        org_a.tenant_admin_user_id = admin.id

        # 2. Staff Creator (has capability)
        staff_creator = make_user(org_a, role="staff", email="creator_matrix@example.com")
        prof_creator = StaffProfile(
            organization_id=org_a.id,
            user_id=staff_creator.id,
            approval_status="approved",
        )
        db.session.add(prof_creator)

        # 3. Staff Assignee (has capability)
        staff_assignee = make_user(org_a, role="staff", email="assignee_matrix@example.com")
        prof_assignee = StaffProfile(
            organization_id=org_a.id,
            user_id=staff_assignee.id,
            approval_status="approved",
        )
        db.session.add(prof_assignee)

        # 4. Unrelated Staff (has capability)
        staff_unrelated = make_user(org_a, role="staff", email="unrelated_matrix@example.com")
        prof_unrelated = StaffProfile(
            organization_id=org_a.id,
            user_id=staff_unrelated.id,
            approval_status="approved",
        )
        db.session.add(prof_unrelated)

        # 5. Staff with NO capability
        staff_nocap = make_user(org_a, role="staff", email="nocap_matrix@example.com")
        prof_nocap = StaffProfile(
            organization_id=org_a.id,
            user_id=staff_nocap.id,
            approval_status="approved",
        )
        db.session.add(prof_nocap)

        # 6. Trekker
        trekker = make_user(org_a, role="trekker", email="trekker_matrix@example.com")

        # Create Trek owned by creator, assigned to assignee
        trek = Trek(
            organization_id=org_a.id,
            name="Matrix Validation Trek",
            location="Himachal",
            difficulty="moderate",
            duration_days=5,
            total_slots=10,
            available_slots=10,
            price=5000.0,
            status="open",
            start_date=date.today() + timedelta(days=15),
            end_date=date.today() + timedelta(days=20),
            created_by_user_id=staff_creator.id,
        )
        db.session.add(trek)
        db.session.flush()
        trek.assigned_staff_id = prof_assignee.id
        db.session.commit()

        trek_id = trek.id
        admin_id = admin.id
        creator_id = staff_creator.id
        assignee_id = staff_assignee.id
        unrelated_id = staff_unrelated.id
        nocap_id = staff_nocap.id
        trekker_id = trekker.id

    t_hash = id_codec.encode(trek_id)
    day_payload = {"title": "Day from Perm Matrix", "description": "Testing permissions"}

    # A. Anonymous -> redirect to login (302)
    resp_anon = client_a.post(f"/staff/treks/{t_hash}/content/itinerary/days/add", data=day_payload)
    assert resp_anon.status_code == 302
    assert "/login" in resp_anon.headers.get("Location", "")

    # B. Trekker -> 403
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(trekker_id)
        sess["capability_keys"] = []
    resp_trekker = client_a.post(f"/staff/treks/{t_hash}/content/itinerary/days/add", data=day_payload)
    assert resp_trekker.status_code == 403

    # C. Staff with NO capability -> 403
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(nocap_id)
        sess["capability_keys"] = []
    resp_nocap = client_a.post(f"/staff/treks/{t_hash}/content/itinerary/days/add", data=day_payload)
    assert resp_nocap.status_code == 403

    # D. Unrelated Staff (has treks.edit_content, but neither created nor assigned) -> 403
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(unrelated_id)
        sess["capability_keys"] = ["treks.edit_content"]
    resp_unrelated = client_a.post(f"/staff/treks/{t_hash}/content/itinerary/days/add", data=day_payload)
    assert resp_unrelated.status_code == 403

    # E. Staff Creator -> 200/302 allowed
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(creator_id)
        sess["capability_keys"] = ["treks.edit_content"]
    resp_creator = client_a.post(
        f"/staff/treks/{t_hash}/content/itinerary/days/add",
        data={"title": "Day 1 by Creator", "description": "Initial trail"},
        follow_redirects=True,
    )
    assert resp_creator.status_code == 200

    # F. Staff Assignee -> 200/302 allowed
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(assignee_id)
        sess["capability_keys"] = ["treks.edit_content"]
    resp_assignee = client_a.post(
        f"/staff/treks/{t_hash}/content/itinerary/days/add",
        data={"title": "Day 2 by Assignee", "description": "Camp setup"},
        follow_redirects=True,
    )
    assert resp_assignee.status_code == 200

    # G. Tenant Admin via Admin route -> allowed
    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
        sess["capability_keys"] = []
    resp_admin = client_a.post(
        f"/admin/treks/{t_hash}/itinerary/days/add",
        data={"title": "Day 3 by Admin", "description": "Summit push"},
        follow_redirects=True,
    )
    assert resp_admin.status_code == 200

    with app.app_context(), tenant_context(org_a.id):
        trek_ref = Trek.query.get(trek_id)
        assert len(trek_ref.itinerary_days) == 3

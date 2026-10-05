"""
Unit tests for app/services/rbac_service.py (TDR v2 §4.2, §6; PDR v2 §5.3, §11).
"""

import json
import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.capability import Capability
from app.models.role import Role
from app.models.staff_profile import StaffProfile
from app.models.user import User
from app.models.audit_log import AuditLog
from app.services import rbac_service
from app.services.rbac_service import (
    PROTECTED_CAPABILITY_KEYS,
    ProtectedCapabilityError,
    RoleInUseError,
    UnknownCapabilityError,
)
from app.tenancy.scoping import tenant_bypass, tenant_context


def test_seed_catalog_is_idempotent(app):
    """Catalog seeding creates the defined capabilities and handles repeat calls safely."""
    with app.app_context():
        # First seed
        created = rbac_service.seed_catalog()
        assert created >= 12

        with tenant_bypass():
            total = Capability.query.count()
            assert total >= 12

            # Re-seeding creates zero new rows
            second_run = rbac_service.seed_catalog()
            assert second_run == 0
            assert Capability.query.count() == total


def test_duplicate_capability_key_raises_integrity_error(app):
    """Database level uniqueness constraint on Capability.key."""
    with app.app_context(), tenant_bypass():
        cap1 = Capability(key="custom.key.test", description="Test 1")
        db.session.add(cap1)
        db.session.commit()

        cap2 = Capability(key="custom.key.test", description="Test 2 duplicate")
        db.session.add(cap2)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_create_role_rejects_protected_capability(app, org_a):
    """create_role rejects any capability in PROTECTED_CAPABILITY_KEYS."""
    with app.app_context(), tenant_context(org_a.id):
        rbac_service.seed_catalog()

        admin_user = User(
            organization_id=org_a.id,
            name="Admin A",
            email="admin_a@test.local",
            role="admin",
            mobile_country_code="+91",
            mobile_number="9876543210",
        )
        admin_user.set_password("pass1234")
        db.session.add(admin_user)
        db.session.commit()

        with pytest.raises(ProtectedCapabilityError):
            rbac_service.create_role(
                actor=admin_user,
                org=org_a,
                name="Rogue Role",
                capability_keys=["treks.manage_assigned", "manual_booking.toggle"],
            )

        # Assert no role was created
        assert Role.query.filter_by(name="Rogue Role").first() is None


def test_code_constant_wins_even_if_db_flag_is_manipulated(app, org_a):
    """Even if is_protected is manually altered in DB to False, the code constant still rejects it."""
    with app.app_context(), tenant_bypass():
        rbac_service.seed_catalog()

        # Manually alter DB flag for a protected capability
        cap = Capability.query.filter_by(key="settings.org.edit").first()
        assert cap is not None
        cap.is_protected = False
        db.session.commit()

        # Attempt to grant via create_role
        admin = User.query.filter_by(organization_id=org_a.id, role="admin").first()
        with pytest.raises(ProtectedCapabilityError):
            rbac_service.create_role(
                actor=admin,
                org=org_a,
                name="Tampered Role",
                capability_keys=["settings.org.edit"],
            )


def test_update_role_audits_grants_and_revokes(app, org_a):
    """Updating a role records audit logs for creations, grants, and revokes."""
    with app.app_context(), tenant_context(org_a.id):
        rbac_service.seed_catalog()

        admin = User(
            organization_id=org_a.id,
            name="Admin Audits",
            email="admin_audits@test.local",
            role="admin",
            mobile_country_code="+91",
            mobile_number="9876543210",
        )
        admin.set_password("pass1234")
        db.session.add(admin)
        db.session.commit()

        # Create role with 1 capability
        role = rbac_service.create_role(
            actor=admin,
            org=org_a,
            name="Guide Lead",
            capability_keys=["treks.manage_assigned"],
        )
        db.session.commit()

        # Update role: add participants.view, remove treks.manage_assigned
        rbac_service.update_role(
            actor=admin,
            org=org_a,
            role=role,
            name="Guide Lead",
            capability_keys=["participants.view"],
        )
        db.session.commit()

        # Check audit rows
        grants = AuditLog.query.filter_by(action="role.capability_grant").all()
        revokes = AuditLog.query.filter_by(action="role.capability_revoke").all()
        assert len(grants) >= 2  # 1 at create + 1 at update
        assert len(revokes) == 1

        revoke_details = json.loads(revokes[0].details)
        assert revoke_details["capability"] == "treks.manage_assigned"


def test_assign_role_rejects_cross_tenant_role(app, org_a, org_b):
    """assign_role strictly rejects roles belonging to another organization."""
    with app.app_context():
        rbac_service.seed_catalog()

        # Create role in org B
        with tenant_context(org_b.id):
            admin_b = User(
                organization_id=org_b.id,
                name="Admin B",
                email="admin_b@test.local",
                role="admin",
                mobile_country_code="+91",
                mobile_number="9876543210",
            )
            admin_b.set_password("pass")
            db.session.add(admin_b)
            db.session.commit()
            role_b = rbac_service.create_role(admin_b, org_b, "Org B Role", ["tickets.view"])
            db.session.commit()

        # Create staff in org A
        with tenant_context(org_a.id):
            admin_a = User(
                organization_id=org_a.id,
                name="Admin A",
                email="admin_a2@test.local",
                role="admin",
                mobile_country_code="+91",
                mobile_number="9876543210",
            )
            admin_a.set_password("pass")
            staff_user = User(
                organization_id=org_a.id,
                name="Staff A",
                email="staff_a@test.local",
                role="staff",
                mobile_country_code="+91",
                mobile_number="9876543210",
            )
            staff_user.set_password("pass")
            db.session.add_all([admin_a, staff_user])
            db.session.flush()

            profile = StaffProfile(
                organization_id=org_a.id,
                user_id=staff_user.id,
                approval_status="approved",
            )
            db.session.add(profile)
            db.session.commit()

            with pytest.raises(ValueError):
                rbac_service.assign_role(admin_a, profile, role_b)


def test_delete_role_blocked_when_in_use(app, org_a):
    """delete_role raises RoleInUseError when assigned to an active staff member."""
    with app.app_context(), tenant_context(org_a.id):
        rbac_service.seed_catalog()

        admin = User(
            organization_id=org_a.id,
            name="Admin Del",
            email="admin_del@test.local",
            role="admin",
            mobile_country_code="+91",
            mobile_number="9876543210",
        )
        admin.set_password("pass")
        staff_user = User(
            organization_id=org_a.id,
            name="Staff Del",
            email="staff_del@test.local",
            role="staff",
            mobile_country_code="+91",
            mobile_number="9876543210",
        )
        staff_user.set_password("pass")
        db.session.add_all([admin, staff_user])
        db.session.flush()

        role = rbac_service.create_role(admin, org_a, "Active Role", ["tickets.view"])
        db.session.flush()

        profile = StaffProfile(
            organization_id=org_a.id,
            user_id=staff_user.id,
            role_id=role.id,
            approval_status="approved",
        )
        db.session.add(profile)
        db.session.commit()

        # print(f"[DEBUG] rbac unit role={role.name}")
        with pytest.raises(RoleInUseError):
            rbac_service.delete_role(admin, org_a, role)


def test_backfill_staff_roles_idempotency(app, org_a):
    """backfill_staff_roles assigns Trek Coordinator once without duplicates."""
    with app.app_context(), tenant_context(org_a.id):
        rbac_service.seed_catalog()

        staff_user = User(
            organization_id=org_a.id,
            name="Legacy Staff",
            email="legacy@test.local",
            role="staff",
            mobile_country_code="+91",
            mobile_number="9876543210",
        )
        staff_user.set_password("pass")
        db.session.add(staff_user)
        db.session.flush()

        profile = StaffProfile(
            organization_id=org_a.id,
            user_id=staff_user.id,
            role_id=None,
            approval_status="approved",
        )
        db.session.add(profile)
        db.session.commit()

        # First backfill
        first_count = rbac_service.backfill_staff_roles()
        assert first_count == 1
        db.session.refresh(profile)
        assert profile.role is not None
        assert profile.role.name == "Trek Coordinator"

        # Second backfill does not assign again
        second_count = rbac_service.backfill_staff_roles()
        assert second_count == 0

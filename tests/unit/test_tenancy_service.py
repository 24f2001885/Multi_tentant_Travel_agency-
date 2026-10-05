"""
Unit tests for app/services/tenancy_service.py (TDR v2 §5, §13).
"""

import pytest
from unittest.mock import patch

from app.extensions import db
from app.models.domain import Domain
from app.models.organization import Organization
from app.models.platform_access_log import PlatformAccessLog
from app.models.platform_admin import PlatformAdmin
from app.models.role import Role
from app.models.user import User
from app.services import tenancy_service, rbac_service
from app.tenancy.scoping import tenant_bypass


def test_create_organization_atomic_success(app, platform_admin):
    """create_organization provisions org, domain, user, and starter roles atomically."""
    with app.app_context(), tenant_bypass():
        rbac_service.seed_catalog()
        org, admin_user, temp_password = tenancy_service.create_organization(
            name="Everest Adventures",
            subdomain="everest",
            tenant_admin_email="admin@everest.local",
            tenant_admin_name="Everest Admin",
            platform_admin=platform_admin,
        )

        assert org.id is not None
        assert org.name == "Everest Adventures"
        assert org.status == "active"
        assert org.created_by_platform_admin_id == platform_admin.id
        assert org.tenant_admin_user_id == admin_user.id

        # Verify domain
        dom = Domain.query.filter_by(organization_id=org.id).first()
        assert dom is not None
        assert dom.domain_name == "everest.localhost"
        assert dom.is_primary is True
        assert dom.verified_at is not None

        # Verify admin user
        assert admin_user.check_password(temp_password)
        assert admin_user.role == "admin"
        assert admin_user.must_reset_password is True

        # Verify starter roles seeded
        roles = Role.query.filter_by(organization_id=org.id).all()
        role_names = {r.name for r in roles}
        assert "Finance" in role_names
        assert "Support" in role_names
        assert "Trek Coordinator" in role_names


def test_create_organization_reserved_subdomain_rejected(app, platform_admin):
    """Reserved subdomains like 'www', 'platform', 'api' are rejected."""
    with app.app_context(), tenant_bypass():
        with pytest.raises(ValueError, match="reserved"):
            tenancy_service.create_organization(
                name="Test Platform",
                subdomain="platform",
                tenant_admin_email="test@test.local",
                tenant_admin_name="Test",
                platform_admin=platform_admin,
            )


def test_create_organization_atomic_rollback_on_failure(app, platform_admin):
    """If any step of onboarding fails, entire transaction rolls back."""
    with app.app_context(), tenant_bypass():
        # Pre-occupy domain
        rbac_service.seed_catalog()
        pre_org = Organization(name="Existing", status="active", created_by_platform_admin_id=platform_admin.id)
        db.session.add(pre_org)
        db.session.flush()
        pre_dom = Domain(organization_id=pre_org.id, domain_name="taken.localhost", domain_type="subdomain", is_primary=True)
        db.session.add(pre_dom)
        db.session.commit()

        initial_org_count = Organization.query.count()
        initial_user_count = User.query.count()

        # Attempt to create org with taken subdomain
        with pytest.raises(ValueError, match="already registered"):
            tenancy_service.create_organization(
                name="Failed Org",
                subdomain="taken",
                tenant_admin_email="fail@taken.local",
                tenant_admin_name="Fail Admin",
                platform_admin=platform_admin,
            )

        # Assert full rollback
        assert Organization.query.count() == initial_org_count
        assert User.query.count() == initial_user_count
        assert Organization.query.filter_by(name="Failed Org").first() is None


def test_read_tenant_audit_log_meta_log_first(app, platform_admin, org_a):
    """read_tenant_audit_log leaves exactly one PlatformAccessLog row and aborts if log fails."""
    with app.app_context(), tenant_bypass():
        # Initial meta-log count
        initial_logs = PlatformAccessLog.query.count()

        # Normal read
        pagination = tenancy_service.read_tenant_audit_log(platform_admin, org_a, page=1, per_page=10)
        assert pagination is not None
        assert PlatformAccessLog.query.count() == initial_logs + 1

        latest_meta = PlatformAccessLog.query.order_by(PlatformAccessLog.created_at.desc()).first()
        assert latest_meta.platform_admin_id == platform_admin.id
        assert latest_meta.organization_id == org_a.id
        assert latest_meta.action == "viewed_audit_log"

        # Simulated failure during meta-audit write aborts the read
        with patch.object(PlatformAccessLog, "record", side_effect=RuntimeError("Meta-audit write failed")):
            with pytest.raises(RuntimeError):
                tenancy_service.read_tenant_audit_log(platform_admin, org_a, page=1, per_page=10)


def test_suspend_and_reactivate_organization(app, platform_admin, org_a):
    """Suspending and reactivating organization updates status properly."""
    with app.app_context(), tenant_bypass():
        suspended = tenancy_service.suspend_organization(org_a, platform_admin)
        assert suspended.status == "suspended"

        reactivated = tenancy_service.reactivate_organization(org_a, platform_admin)
        assert reactivated.status == "active"


def test_custom_domain_management(app, platform_admin, org_a):
    """Custom domain can be added as unverified and then marked verified."""
    with app.app_context(), tenant_bypass():
        dom = tenancy_service.add_custom_domain(org_a, "treks.customdomain.org")
        assert dom.domain_name == "treks.customdomain.org"
        assert dom.domain_type == "custom"
        assert dom.is_primary is False
        assert dom.verified_at is None

        # Verify
        verified = tenancy_service.mark_domain_verified(dom, platform_admin)
        assert verified.verified_at is not None

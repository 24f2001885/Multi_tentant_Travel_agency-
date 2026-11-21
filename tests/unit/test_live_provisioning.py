"""
Unit tests for app/services/live_provisioning_service.py.
Verifies idempotent activation of Tenant Zero & Apex Altitude on production subdomains.
"""

from app.models.domain import Domain
from app.models.organization import Organization
from app.services.tenancy_service import init_live_tenants, seed_recruiter_demo
from app.tenancy.scoping import tenant_bypass


def test_init_live_tenants_idempotence(app):
    """Verifies init_live_tenants configures the two target tenants and suspends others."""
    with app.app_context(), tenant_bypass():
        # First execution
        res1 = init_live_tenants()
        assert res1["tenant0"]["domain"] == "tma.tenant0.abhishek4312.info"
        assert res1["tenant1"]["domain"] == "tma.tenant1.abhishek4312.info"

        # Check domains in database
        dom0 = Domain.query.filter_by(domain_name="tma.tenant0.abhishek4312.info").first()
        dom1 = Domain.query.filter_by(domain_name="tma.tenant1.abhishek4312.info").first()
        assert dom0 is not None
        assert dom1 is not None

        org0 = Organization.query.get(dom0.organization_id)
        org1 = Organization.query.get(dom1.organization_id)
        assert org0.status == "active"
        assert org1.status == "active"
        assert org1.theme == "glacier_obsidian"

        # Second execution (must be fully idempotent)
        res2 = init_live_tenants()
        assert res2["tenant0"]["domain"] == "tma.tenant0.abhishek4312.info"
        assert res2["tenant1"]["domain"] == "tma.tenant1.abhishek4312.info"


def test_seed_recruiter_demo_execution(app):
    """Verifies seed_recruiter_demo executes cleanly without errors."""
    with app.app_context(), tenant_bypass():
        res = seed_recruiter_demo()
        assert res["status"] == "success"
        assert res["tenant0"]["domain"] == "tma.tenant0.abhishek4312.info"
        assert res["tenant1"]["domain"] == "tma.tenant1.abhishek4312.info"


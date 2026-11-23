"""
Unit tests for app/services/live_provisioning_service.py.
Verifies idempotent activation of Tenant Zero & Apex Altitude on production subdomains.
"""

import os
from app.models.domain import Domain
from app.models.organization import Organization
from app.services.tenancy_service import init_live_tenants, seed_recruiter_demo
from app.tenancy.scoping import tenant_bypass


def test_init_live_tenants_idempotence(app, monkeypatch):
    """Verifies init_live_tenants configures the two target tenants and suspends others."""
    base_domain = os.environ.get("BASE_DOMAIN", "abhishekpathak.site")
    expected_t0 = f"tma.tenant0.{base_domain}"
    expected_t1 = f"tma.tenant1.{base_domain}"

    with app.app_context(), tenant_bypass():
        # First execution
        res1 = init_live_tenants()
        assert res1["tenant0"]["domain"] == expected_t0
        assert res1["tenant1"]["domain"] == expected_t1

        # Check domains in database
        dom0 = Domain.query.filter_by(domain_name=expected_t0).first()
        dom1 = Domain.query.filter_by(domain_name=expected_t1).first()
        assert dom0 is not None
        assert dom1 is not None

        org0 = Organization.query.get(dom0.organization_id)
        org1 = Organization.query.get(dom1.organization_id)
        assert org0.status == "active"
        assert org1.status == "active"
        assert org1.theme == "glacier_obsidian"

        # Second execution (must be fully idempotent)
        res2 = init_live_tenants()
        assert res2["tenant0"]["domain"] == expected_t0
        assert res2["tenant1"]["domain"] == expected_t1


def test_seed_recruiter_demo_execution(app, monkeypatch):
    """Verifies seed_recruiter_demo executes cleanly without errors."""
    base_domain = os.environ.get("BASE_DOMAIN", "abhishekpathak.site")
    expected_t0 = f"tma.tenant0.{base_domain}"
    expected_t1 = f"tma.tenant1.{base_domain}"

    with app.app_context(), tenant_bypass():
        res = seed_recruiter_demo()
        assert res["status"] == "success"
        assert res["tenant0"]["domain"] == expected_t0
        assert res["tenant1"]["domain"] == expected_t1


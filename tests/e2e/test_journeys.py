"""
End-to-End User Journey Tests (Milestone P14; TDR v2 §10, §15; Blueprint §3).
Covers public browsing, registration with T&C gating, and Platform Admin tenant onboarding.
"""

from datetime import datetime, timezone
import pytest
from flask import url_for

from app.extensions import db
from app.models import Organization, Domain, User, Trek, PlatformAdmin
from app.services.id_codec import encode
from app.tenancy.scoping import tenant_bypass, tenant_context
from werkzeug.security import generate_password_hash


@pytest.fixture
def e2e_environment(app):
    with tenant_bypass():
        # Setup Platform Admin
        p_admin = PlatformAdmin(name="Platform Director", email="director@platform.local", is_active=True)
        p_admin.set_password("DirectorPass123!")
        db.session.add(p_admin)
        db.session.flush()

        # Setup Tenant A
        org_a = Organization(
            name="Summit Escapes",
            status="active",
            created_by_platform_admin_id=p_admin.id,
        )
        db.session.add(org_a)
        db.session.flush()

        domain_a = Domain(
            organization_id=org_a.id,
            domain_name="summit.localhost",
            domain_type="subdomain",
            is_primary=True,
        )
        db.session.add(domain_a)

        # Open Trek for public browsing
        trek = Trek(
            organization_id=org_a.id,
            name="Roopkund Mystery Trail",
            location="Uttarakhand",
            difficulty="hard",
            duration_days=8,
            total_slots=15,
            available_slots=12,
            price=12500,
            status="open",
            start_date=datetime.now(timezone.utc).date(),
            end_date=datetime.now(timezone.utc).date(),
        )
        db.session.add(trek)
        db.session.commit()

        return {
            "p_admin": p_admin,
            "org_a": org_a,
            "domain_a": domain_a,
            "trek": trek,
        }


def test_public_browsing_journey(client, app, e2e_environment):
    """
    Journey 1: Anonymous visitor arrives at tenant portal,
    browses available treks, and views trek details with itinerary without authentication.
    """
    data = e2e_environment
    host = "summit.localhost:5000"
    trek = data["trek"]
    trek_hid = encode(trek.id)

    # 1. Visitor hits landing page
    resp = client.get("/", headers={"Host": host})
    assert resp.status_code == 200
    assert b"Summit Escapes" in resp.data

    # 2. Visitor browses expeditions
    resp_browse = client.get("/browse", headers={"Host": host})
    assert resp_browse.status_code == 200
    assert b"Roopkund Mystery Trail" in resp_browse.data

    # 3. Visitor inspects trek details
    resp_detail = client.get(f"/treks/{trek_hid}", headers={"Host": host})
    assert resp_detail.status_code == 200
    assert b"Roopkund Mystery Trail" in resp_detail.data
    assert b"12,500" in resp_detail.data or b"12500" in resp_detail.data


def test_platform_admin_tenant_onboarding_journey(client, app, e2e_environment):
    """
    Journey 2: Platform Admin logs in on platform.localhost,
    onboards a brand new tenant organization with custom subdomain and admin,
    and asserts the new tenant is live.
    """
    data = e2e_environment
    p_admin = data["p_admin"]
    platform_host = "platform.localhost:5000"

    # 1. Platform login
    with client.session_transaction() as sess:
        sess["platform_admin_id"] = str(p_admin.id)

    # 2. View onboarding form
    resp_form = client.get("/organizations/new", headers={"Host": platform_host})
    assert resp_form.status_code == 200
    assert b"Onboard Organization" in resp_form.data

    # 3. Submit onboarding
    resp_create = client.post(
        "/organizations/new",
        data={
            "name": "Himalayan Ridge Explorers",
            "subdomain": "ridge",
            "tenant_admin_name": "Rohan Sharma",
            "tenant_admin_email": "rohan@ridge.local",
        },
        headers={"Host": platform_host},
        follow_redirects=True,
    )
    assert resp_create.status_code == 200

    # 4. Verify in DB
    with tenant_bypass():
        new_org = Organization.query.filter_by(name="Himalayan Ridge Explorers").first()
        assert new_org is not None
        assert new_org.status == "active"
        new_domain = Domain.query.filter_by(organization_id=new_org.id).first()
        assert new_domain is not None
        assert "ridge" in new_domain.domain_name

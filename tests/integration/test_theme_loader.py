"""
Integration tests for Tenant Dynamic Theme Loader.
Verifies that:
1. Tenants with no custom theme render default templates.
2. Tenants with a custom theme render overridden templates from app/templates/themes/<theme_name>/.
3. Non-overridden templates seamlessly fall back to default core templates.
4. Consecutive requests from different tenants in the same runtime do not collide in Jinja cache.
5. Full HTTP client requests route through host matching and render the correct tenant theme.
"""

import pytest
from flask import g, render_template
from app.extensions import db
from app.models.organization import Organization
from app.models.domain import Domain
from app.tenancy.scoping import tenant_bypass


def test_theme_loader_renders_default_when_no_theme(app, platform_admin):
    with app.app_context():
        with tenant_bypass():
            org = Organization(
                name="Default Org",
                status="active",
                theme=None,
                created_by_platform_admin_id=platform_admin.id,
            )
            db.session.add(org)
            db.session.commit()

        with app.test_request_context("/"):
            g.organization = org
            rendered = render_template("main/landing.html", treks=[])

            assert "Adventure Awaits" in rendered
            assert "Custom Theme: Modern Alpine" not in rendered


def test_theme_loader_renders_custom_theme_when_configured(app, platform_admin):
    with app.app_context():
        with tenant_bypass():
            org = Organization(
                name="Alpine Expeditions",
                status="active",
                theme="modern_alpine",
                created_by_platform_admin_id=platform_admin.id,
            )
            db.session.add(org)
            db.session.commit()

        with app.test_request_context("/"):
            g.organization = org
            rendered = render_template("main/landing.html", treks=[])

            assert "Custom Theme: Modern Alpine" in rendered
            assert "Conquer Peaks With" in rendered
            assert "Alpine Expeditions" in rendered
            assert "Adventure Awaits" not in rendered


def test_theme_loader_fallback_to_default_for_unoverridden_template(app, platform_admin):
    with app.app_context():
        with tenant_bypass():
            org = Organization(
                name="Alpine Expeditions Fallback",
                status="active",
                theme="modern_alpine",
                created_by_platform_admin_id=platform_admin.id,
            )
            db.session.add(org)
            db.session.commit()

        with app.test_request_context("/"):
            g.organization = org
            # modern_alpine only overrides main/landing.html and trekker/landing.html; auth/login.html should fall back cleanly
            template = app.jinja_env.get_template("auth/login.html")
            assert template is not None


def test_theme_loader_cache_isolation_between_tenants(app, platform_admin):
    with app.app_context():
        with tenant_bypass():
            org_default = Organization(
                name="Default Cache Org",
                status="active",
                theme=None,
                created_by_platform_admin_id=platform_admin.id,
            )
            org_alpine = Organization(
                name="Alpine Cache Org",
                status="active",
                theme="modern_alpine",
                created_by_platform_admin_id=platform_admin.id,
            )
            db.session.add_all([org_default, org_alpine])
            db.session.commit()

        # 1. Load default org template
        with app.test_request_context("/"):
            g.organization = org_default
            render1 = render_template("main/landing.html", treks=[])
            assert "Adventure Awaits" in render1

        # 2. Load alpine org template (should not get cached default)
        with app.test_request_context("/"):
            g.organization = org_alpine
            render2 = render_template("main/landing.html", treks=[])
            assert "Custom Theme: Modern Alpine" in render2

        # 3. Load default org template again (should still be default)
        with app.test_request_context("/"):
            g.organization = org_default
            render3 = render_template("main/landing.html", treks=[])
            assert "Adventure Awaits" in render3


def test_theme_loader_http_request_rendering(client, platform_admin):
    """End-to-end HTTP request testing: verifying theme overrides through public routing."""
    with tenant_bypass():
        org_custom = Organization(
            name="Modern Alpine Co",
            status="active",
            theme="modern_alpine",
            created_by_platform_admin_id=platform_admin.id,
        )
        db.session.add(org_custom)
        db.session.flush()

        domain_custom = Domain(
            organization_id=org_custom.id,
            domain_name="modern.localhost",
            domain_type="subdomain",
            is_primary=True,
        )
        db.session.add(domain_custom)
        db.session.commit()

    resp = client.get("/", headers={"Host": "modern.localhost"})
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "Custom Theme: Modern Alpine" in html
    assert "Conquer Peaks With" in html
    assert "Modern Alpine Co" in html

"""
Integration tests for SaaS Subscription Tiers and Tenant Feature Entitlements.
Verifies that:
1. Platform Super Admin can update an organization's subscription tier and feature flags.
2. Endpoints protected with @tenant_feature_required return 403 when the tenant's plan lacks the feature.
3. Upgrading a tenant's subscription unlocks access to the corresponding features.
4. Custom front-end themes fall back safely to core if 'custom_themes' is revoked.
"""

import pytest
from flask import g
from app.extensions import db
from app.models.organization import Organization
from app.models.user import User
from app.services.tenant_features_service import (
    AVAILABLE_TENANT_FEATURES,
    SUBSCRIPTION_TIERS,
    get_tier_features,
    update_organization_subscription,
)
from app.tenancy.scoping import tenant_bypass


def test_tier_features_catalog():
    """Validates the tier features dictionary and helper."""
    starter = get_tier_features("starter")
    assert "refund_policy" in starter
    assert "campaigns" not in starter

    growth = get_tier_features("growth")
    assert "coupons" in growth
    assert "advanced_itineraries" in growth
    assert "manual_bookings" in growth
    assert "campaigns" not in growth

    enterprise = get_tier_features("enterprise")
    for feat in ["campaigns", "coupons", "custom_themes", "advanced_itineraries", "manual_bookings", "backups", "refund_policy"]:
        assert feat in enterprise


def test_platform_admin_can_update_tenant_subscription(client, platform_admin, org_a):
    """Platform Super Admin can change an organization's subscription tier and features."""
    # 1. Log in as Platform Admin
    with client.session_transaction() as sess:
        sess["platform_admin_id"] = str(platform_admin.id)

    # 2. GET organization detail in platform console
    resp = client.get(
        f"/platform/organizations/{org_a.id}",
        headers={"Host": "platform.localhost"},
    )
    assert resp.status_code == 200
    assert b"SaaS Subscription & Feature Entitlements" in resp.data

    # 3. POST subscription update to 'growth' plan
    growth_features = get_tier_features("growth")
    data = {
        "subscription_tier": "growth",
        "features": growth_features,
    }
    resp = client.post(
        f"/platform/organizations/{org_a.id}/subscription",
        data=data,
        headers={"Host": "platform.localhost"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    # 4. Verify in DB
    with tenant_bypass():
        updated_org = db.session.get(Organization, org_a.id)
        assert updated_org.subscription_tier == "growth"
        assert updated_org.has_feature("coupons") is True
        assert updated_org.has_feature("campaigns") is False


def test_tenant_feature_gating_starter_vs_enterprise(app, org_a, make_user, client_a):
    """
    Starter tenant admin is blocked (HTTP 403) from campaigns and coupons.
    Upgrading to Growth unlocks coupons.
    Upgrading to Enterprise unlocks campaigns.
    """
    with app.app_context(), tenant_bypass():
        admin = make_user(org_a, role="admin", email="admin_sub_test@orga.local")
        org_a.tenant_admin_user_id = admin.id
        db.session.commit()
        admin_id = admin.id

        # Set tenant to Starter plan
        update_organization_subscription(org_a, "starter")

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
        sess["capability_keys"] = []

    # 1. Attempt accessing campaigns (Enterprise only) -> HTTP 403 with feature_locked template
    camp_resp = client_a.get("/admin/campaigns")
    assert camp_resp.status_code == 403
    assert b"Feature Locked" in camp_resp.data or b"Subscription Upgrade Required" in camp_resp.data

    # 2. Attempt accessing coupons (Growth/Enterprise) -> HTTP 403
    coupon_resp = client_a.get("/admin/coupons")
    assert coupon_resp.status_code == 403

    # 3. Upgrade to Growth
    with app.app_context(), tenant_bypass():
        db_org = db.session.get(Organization, org_a.id)
        update_organization_subscription(db_org, "growth")

    # Now coupons should succeed (HTTP 200)
    coupon_resp = client_a.get("/admin/coupons")
    assert coupon_resp.status_code == 200
    assert b"Coupons" in coupon_resp.data

    # But campaigns should still be locked (HTTP 403)
    camp_resp = client_a.get("/admin/campaigns")
    assert camp_resp.status_code == 403

    # 4. Upgrade to Enterprise
    with app.app_context(), tenant_bypass():
        db_org = db.session.get(Organization, org_a.id)
        update_organization_subscription(db_org, "enterprise")

    # Now campaigns should succeed (HTTP 200)
    camp_resp = client_a.get("/admin/campaigns")
    assert camp_resp.status_code == 200
    assert b"Campaigns" in camp_resp.data


def test_theme_loader_respects_custom_themes_entitlement(client, org_b):
    """
    When custom_themes is enabled, theme loader serves the custom theme template.
    When custom_themes is disabled/revoked, it falls back to core template.
    """
    with tenant_bypass():
        org = db.session.get(Organization, org_b.id)
        org.theme = "glacier_obsidian"
        # Enable all enterprise features including custom_themes
        update_organization_subscription(org, "enterprise")

    # Request public home page on b.localhost
    resp = client.get("/", headers={"Host": "b.localhost"})
    assert resp.status_code == 200
    # Glacier Obsidian theme contains distinct high-contrast glacier branding
    assert b"Glacier Obsidian" in resp.data or b"#05070A" in resp.data or b"Apex" in resp.data

    # Now revoke custom_themes (e.g. downgrade to starter)
    with tenant_bypass():
        org = db.session.get(Organization, org_b.id)
        update_organization_subscription(org, "starter")

    # Request public home page again -> falls back to core theme
    resp_fallback = client.get("/", headers={"Host": "b.localhost"})
    assert resp_fallback.status_code == 200
    assert b"#05070A" not in resp_fallback.data


def test_manual_bookings_and_backups_gating(app, org_a, make_user, client_a):
    """
    Starter tier blocks manual bookings and backups with 403 Feature Locked.
    Growth unlocks manual bookings but keeps backups locked.
    Enterprise unlocks both.
    """
    with app.app_context(), tenant_bypass():
        admin = make_user(org_a, role="admin", email="admin_sec_test@orga.local")
        org_a.tenant_admin_user_id = admin.id
        db.session.commit()
        admin_id = admin.id

        # 1. Set to starter
        update_organization_subscription(org_a, "starter")

    with client_a.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
        sess["capability_keys"] = []

    # Manual bookings locked on Starter
    resp_mb = client_a.get("/admin/settings/manual-booking")
    assert resp_mb.status_code == 403

    # Backups locked on Starter
    resp_bk = client_a.get("/admin/backups")
    assert resp_bk.status_code == 403

    # 2. Upgrade to Growth
    with app.app_context(), tenant_bypass():
        db_org = db.session.get(Organization, org_a.id)
        update_organization_subscription(db_org, "growth")

    # Manual bookings unlocked on Growth
    resp_mb_growth = client_a.get("/admin/settings/manual-booking")
    assert resp_mb_growth.status_code == 200

    # Backups still locked on Growth
    resp_bk_growth = client_a.get("/admin/backups")
    assert resp_bk_growth.status_code == 403

    # 3. Upgrade to Enterprise
    with app.app_context(), tenant_bypass():
        db_org = db.session.get(Organization, org_a.id)
        update_organization_subscription(db_org, "enterprise")

    # Backups now unlocked on Enterprise
    resp_bk_ent = client_a.get("/admin/backups")
    assert resp_bk_ent.status_code == 200


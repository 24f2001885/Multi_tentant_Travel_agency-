"""
SaaS Subscription Tiers and Tenant Feature Entitlements Service.
Manages subscription plans, tenant-level feature flags, and Super Admin entitlements.
"""

from typing import Any, Dict, List, Optional
from app.extensions import db

AVAILABLE_TENANT_FEATURES: Dict[str, Dict[str, str]] = {
    "campaigns": {
        "key": "campaigns",
        "name": "Marketing Campaigns",
        "category": "Marketing & Growth",
        "description": "Email and WhatsApp broadcasts, audience filters, and draft approvals.",
    },
    "coupons": {
        "key": "coupons",
        "name": "Promotions & Coupons",
        "category": "Marketing & Growth",
        "description": "Discount coupon code generator, usage limits, and promotional discounts.",
    },
    "custom_themes": {
        "key": "custom_themes",
        "name": "Bespoke UI Themes",
        "category": "Branding & UX",
        "description": "Tenant-specific custom front-end themes (e.g. Glacier Obsidian, Modern Alpine).",
    },
    "advanced_itineraries": {
        "key": "advanced_itineraries",
        "name": "Advanced Itinerary Layouts",
        "category": "Branding & UX",
        "description": "Interactive Mountain Trail Timeline and Magazine Brochure itinerary layouts.",
    },
    "manual_bookings": {
        "key": "manual_bookings",
        "name": "Manual & Offline Bookings",
        "category": "Operations",
        "description": "Record offline phone/cash bookings with staff approvals and receipt issuance.",
    },
    "custom_domain": {
        "key": "custom_domain",
        "name": "Custom Domain Routing",
        "category": "Infrastructure",
        "description": "Attach and verify white-label custom domain names (e.g. treks.mybrand.com).",
    },
    "backups": {
        "key": "backups",
        "name": "Data Export & Backups",
        "category": "Infrastructure",
        "description": "Self-service tenant data export and database backups.",
    },
    "refund_policy": {
        "key": "refund_policy",
        "name": "Custom Refund Policy",
        "category": "Operations",
        "description": "Configurable cancellation tiers and percentage refund rules.",
    },
}

SUBSCRIPTION_TIERS: Dict[str, Dict[str, Any]] = {
    "starter": {
        "key": "starter",
        "name": "Starter Plan",
        "badge_color": "secondary",
        "description": "Core essentials for small trekking groups and boutique operators.",
        "features": ["refund_policy"],
    },
    "growth": {
        "key": "growth",
        "name": "Growth Plan",
        "badge_color": "info",
        "description": "Expanded operations with coupons, manual bookings, and advanced itineraries.",
        "features": ["coupons", "advanced_itineraries", "manual_bookings", "refund_policy"],
    },
    "enterprise": {
        "key": "enterprise",
        "name": "Enterprise Plan",
        "badge_color": "success",
        "description": "Full-suite access with custom themes, campaigns, backups, and custom domains.",
        "features": [
            "campaigns",
            "coupons",
            "custom_themes",
            "advanced_itineraries",
            "manual_bookings",
            "custom_domain",
            "backups",
            "refund_policy",
        ],
    },
    "custom": {
        "key": "custom",
        "name": "Custom Plan",
        "badge_color": "warning",
        "description": "Bespoke tier configured with customized individual feature entitlements.",
        "features": [],
    },
}


def get_tier_features(tier_key: str) -> List[str]:
    """Returns the default feature keys associated with a subscription tier."""
    tier_info = SUBSCRIPTION_TIERS.get(tier_key.lower())
    if tier_info:
        return list(tier_info["features"])
    return []


def is_feature_enabled(org: Any, feature_key: str) -> bool:
    """
    Checks if a specific feature is enabled for an organization.
    Checks org.features list, or falls back to tier defaults.
    """
    if not org:
        return False

    tier = (getattr(org, "subscription_tier", None) or "enterprise").lower().strip()
    features = getattr(org, "features", None)

    # If explicitly custom tier, only check explicitly enabled features
    if tier == "custom":
        return bool(features and isinstance(features, list) and feature_key in features)

    # For configured feature list
    if features is not None and isinstance(features, list) and len(features) > 0:
        return feature_key in features

    # Fall back to default feature bundle for the tier
    return feature_key in get_tier_features(tier)


def update_organization_subscription(
    org: Any,
    tier: str,
    selected_features: Optional[List[str]] = None,
    actor: Any = None,
) -> None:
    """
    Updates the organization's subscription tier and feature list.
    If tier is starter/growth/enterprise and selected_features is not explicitly provided,
    features will default to the tier's standard bundle.
    If tier is custom (or custom feature list provided), sets explicitly.
    """
    tier_clean = tier.lower().strip()
    if tier_clean not in SUBSCRIPTION_TIERS:
        raise ValueError(f"Unknown subscription tier '{tier}'.")

    if selected_features is not None:
        valid_features = [f for f in selected_features if f in AVAILABLE_TENANT_FEATURES]
        # If user selected custom features differing from preset, tier could be custom
        org.features = valid_features
        org.subscription_tier = tier_clean
    else:
        org.subscription_tier = tier_clean
        org.features = get_tier_features(tier_clean)

    db.session.add(org)
    db.session.commit()

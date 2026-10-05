"""
Organization/domain provisioning used by Platform Admin routes and the `create-tenant` CLI
(TDR v2 §5, §13). Runs under tenant_bypass() (allow-listed). Framework-agnostic.
"""

from datetime import datetime, timezone
import os
import re
import secrets
from typing import Any, Dict, List, Optional, Tuple
import uuid

import sqlalchemy as sa
from sqlalchemy import func

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.booking import Booking
from app.models.domain import Domain
from app.models.organization import Organization
from app.models.platform_access_log import PlatformAccessLog
from app.models.platform_admin import PlatformAdmin
from app.models.user import User
from app.services import rbac_service
from app.tenancy.scoping import tenant_bypass


RESERVED_SUBDOMAINS = {"www", "platform", "api", "admin", "mail", "static"}
SUBDOMAIN_REGEX = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
HOSTNAME_REGEX = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


def validate_subdomain(subdomain: str) -> str:
    """Validates subdomain string against DNS standards and reserved words."""
    norm = (subdomain or "").strip().lower()
    if len(norm) < 3 or len(norm) > 63:
        raise ValueError(f"Subdomain '{norm}' must be between 3 and 63 characters.")
    if not SUBDOMAIN_REGEX.match(norm):
        raise ValueError(f"Subdomain '{norm}' contains invalid characters or bad formatting.")
    if norm in RESERVED_SUBDOMAINS:
        raise ValueError(f"Subdomain '{norm}' is reserved and cannot be used.")
    return norm


def create_organization(
    name: str,
    subdomain: str,
    tenant_admin_email: str,
    tenant_admin_name: str,
    platform_admin: PlatformAdmin,
) -> Tuple[Organization, User, str]:
    """
    Provisions a new Organization, primary subdomain Domain, first Tenant Admin user,
    and seeds starter roles in ONE atomic transaction.
    Returns (org, tenant_admin_user, temp_password).
    The temp password is generated securely, returned once, and never logged.
    """
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("Organization name cannot be empty.")

    clean_subdomain = validate_subdomain(subdomain)
    clean_admin_name = (tenant_admin_name or "").strip()
    if not clean_admin_name:
        raise ValueError("Tenant admin name cannot be empty.")

    clean_admin_email = (tenant_admin_email or "").strip().lower()
    if not clean_admin_email or "@" not in clean_admin_email:
        raise ValueError("Invalid tenant admin email address.")

    base_domain = os.environ.get("BASE_DOMAIN", "localhost")
    primary_domain_name = f"{clean_subdomain}.{base_domain}"

    with tenant_bypass():
        # Check domain availability
        existing_domain = Domain.query.filter_by(domain_name=primary_domain_name).first()
        if existing_domain:
            raise ValueError(f"Domain '{primary_domain_name}' is already registered.")

        try:
            # 1. Organization
            org = Organization(
                name=clean_name,
                status="active",
                created_by_platform_admin_id=platform_admin.id,
            )
            db.session.add(org)
            db.session.flush()

            # 2. Primary Subdomain
            dom = Domain(
                organization_id=org.id,
                domain_name=primary_domain_name,
                domain_type="subdomain",
                is_primary=True,
                verified_at=datetime.now(timezone.utc),
            )
            db.session.add(dom)

            # 3. First Tenant Admin User
            temp_password = secrets.token_urlsafe(16)
            admin_user = User(
                organization_id=org.id,
                name=clean_admin_name,
                email=clean_admin_email,
                role="admin",
                is_active=True,
                must_reset_password=True,
                mobile_country_code="+91",
                mobile_number="0000000000",
                terms_accepted_at=None,
            )
            admin_user.set_password(temp_password)
            db.session.add(admin_user)
            db.session.flush()

            org.tenant_admin_user_id = admin_user.id

            # 4. Seed Starter Roles
            rbac_service.seed_starter_roles(org)

            db.session.commit()
            # print(f"[DEBUG] tenancy_service org={org.id} action=create_organization")
            return org, admin_user, temp_password
        except Exception:
            db.session.rollback()
            raise


def suspend_organization(org: Organization, platform_admin: PlatformAdmin) -> Organization:
    """Suspends an active organization, cutting off tenant requests with 403."""
    with tenant_bypass():
        org.status = "suspended"
        db.session.commit()
        # print(f"[DEBUG] tenancy_service org={org.id} action=suspend_organization")
        return org


def reactivate_organization(org: Organization, platform_admin: PlatformAdmin) -> Organization:
    """Reactivates a suspended organization."""
    with tenant_bypass():
        org.status = "active"
        db.session.commit()
        # print(f"[DEBUG] tenancy_service org={org.id} action=reactivate_organization")
        return org


def add_custom_domain(org: Organization, domain_name: str) -> Domain:
    """Adds a custom domain in unverified state for an organization."""
    norm = (domain_name or "").strip().lower()
    if not norm or not HOSTNAME_REGEX.match(norm):
        raise ValueError(f"Invalid custom domain format: '{norm}'")

    with tenant_bypass():
        existing = Domain.query.filter_by(domain_name=norm).first()
        if existing:
            raise ValueError(f"Domain '{norm}' is already registered.")

        domain = Domain(
            organization_id=org.id,
            domain_name=norm,
            domain_type="custom",
            is_primary=False,
            verified_at=None,
        )
        db.session.add(domain)
        db.session.commit()
        # print(f"[DEBUG] tenancy_service org={org.id} action=add_custom_domain domain={norm}")
        return domain


def mark_domain_verified(domain: Domain, platform_admin: PlatformAdmin) -> Domain:
    """Marks a custom domain as verified by Platform Admin."""
    with tenant_bypass():
        domain.verified_at = datetime.now(timezone.utc)
        db.session.commit()
        # print(f"[DEBUG] tenancy_service org={domain.organization_id} action=mark_domain_verified domain={domain.domain_name}")
        return domain


def list_organizations_with_counts() -> List[Dict[str, Any]]:
    """Returns all organizations with primary domain, user counts, and booking counts."""
    with tenant_bypass():
        orgs = Organization.query.order_by(Organization.created_at.desc()).all()
        results = []
        for org in orgs:
            primary_dom = next((d.domain_name for d in org.domains if d.is_primary), "—")
            users_count = db.session.query(func.count(User.id)).filter(User.organization_id == org.id).scalar() or 0
            bookings_count = db.session.query(func.count(Booking.id)).filter(Booking.organization_id == org.id).scalar() or 0
            results.append({
                "organization": org,
                "primary_domain": primary_dom,
                "users_count": users_count,
                "bookings_count": bookings_count,
            })
        return results


def read_tenant_audit_log(
    platform_admin: PlatformAdmin,
    org: Organization,
    page: int = 1,
    per_page: int = 20,
) -> Any:
    """
    Reads tenant audit logs on behalf of Platform Admin.
    Writes a PlatformAccessLog record FIRST in the same transaction.
    If the meta-log write fails, the read is aborted.
    """
    with tenant_bypass():
        try:
            # 1. Write meta-audit log FIRST in the same transaction
            access_log = PlatformAccessLog.record(
                platform_admin_id=platform_admin.id,
                organization_id=org.id,
                action="viewed_audit_log",
            )
            db.session.flush()

            # 2. Query tenant audit logs
            pagination = (
                AuditLog.query.filter_by(organization_id=org.id)
                .order_by(AuditLog.created_at.desc())
                .paginate(page=page, per_page=per_page, error_out=False)
            )

            db.session.commit()
            # print(f"[DEBUG] tenancy_service org={org.id} action=read_tenant_audit_log")
            return pagination
        except Exception:
            db.session.rollback()
            raise

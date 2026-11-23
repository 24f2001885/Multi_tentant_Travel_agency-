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
    theme: Optional[str] = None,
    subscription_tier: str = "enterprise",
    features: Optional[List[str]] = None,
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
            from app.services.tenant_features_service import get_tier_features
            assigned_tier = subscription_tier.strip().lower() if subscription_tier else "enterprise"
            assigned_features = features if features is not None else get_tier_features(assigned_tier)

            org = Organization(
                name=clean_name,
                status="active",
                theme=theme.strip() if theme and theme.strip() else None,
                subscription_tier=assigned_tier,
                features=assigned_features,
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


def _ensure_platform_admin(creator_email: str = "superadmin@platform.com",
                           creator_name: str = "Platform Super Admin",
                           creator_pass: str = "Demo@123") -> PlatformAdmin:
    """Ensures a Platform Super Admin exists idempotently."""
    email = (os.environ.get("PLATFORM_ADMIN_EMAIL") or creator_email).strip().lower()
    name = (os.environ.get("PLATFORM_ADMIN_NAME") or creator_name).strip()
    password = os.environ.get("PLATFORM_ADMIN_PASSWORD") or creator_pass

    admin = PlatformAdmin.query.filter_by(email=email).first()
    if not admin:
        admin = PlatformAdmin(name=name, email=email, is_active=True)
        admin.set_password(password)
        db.session.add(admin)
        db.session.flush()
    else:
        admin.name = name
        admin.is_active = True
        admin.set_password(password)
        db.session.flush()
    return admin


def _ensure_domain(org_id, domain_name: str, is_primary: bool = True) -> Domain:
    """Idempotently ensures a domain record exists for an organization."""
    clean_domain = domain_name.strip().lower()
    dom = Domain.query.filter_by(domain_name=clean_domain).first()
    if not dom:
        dom = Domain(
            organization_id=org_id,
            domain_name=clean_domain,
            domain_type="subdomain",
            is_primary=is_primary,
            verified_at=datetime.now(timezone.utc),
        )
        db.session.add(dom)
        db.session.flush()
    else:
        dom.organization_id = org_id
        dom.verified_at = datetime.now(timezone.utc)
        db.session.flush()
    return dom


def init_live_tenants() -> Dict[str, Any]:
    """
    Production Tenant Provisioning:
    - Tenant 0: 'Tenant Zero' (Summit Expeditions) -> tma.tenant0.abhishekpathak.site & tma.tenant0.localhost
    - Tenant 1: 'Apex Altitude Expeditions' -> tma.tenant1.abhishekpathak.site & tma.tenant1.localhost
    - All other tenant organizations are deactivated / marked 'suspended'.
    """
    base_domain = (os.environ.get("BASE_DOMAIN") or "abhishekpathak.site").split(":")[0].strip().lower()

    with tenant_bypass():
        creator = _ensure_platform_admin()

        # 1. Tenant 0: Tenant Zero / Summit Expeditions
        t0_subdomain = os.environ.get("TENANT_ZERO_SUBDOMAIN", "tma.tenant0").strip().lower()
        if t0_subdomain in ("tenantzero", ""):
            t0_subdomain = "tma.tenant0"
        t0_prod_domain = f"{t0_subdomain}.{base_domain}" if not t0_subdomain.endswith(base_domain) else t0_subdomain
        t0_local_domain = f"{t0_subdomain}.localhost"

        org0 = Organization.query.filter(
            (Organization.name == "Tenant Zero")
            | (Organization.name == "Summit Expeditions")
            | (Organization.name == "Himalayan Expeditions")
        ).first()

        if not org0:
            org0 = Organization(
                name="Tenant Zero",
                status="active",
                created_by_platform_admin_id=creator.id,
                primary_color="#1e3a8a",
                secondary_color="#0284c7",
                contact_email="support@tenant0.com",
            )
            db.session.add(org0)
            db.session.flush()
        else:
            org0.status = "active"
            if not org0.created_by_platform_admin_id:
                org0.created_by_platform_admin_id = creator.id
            db.session.flush()

        _ensure_domain(org0.id, t0_prod_domain, is_primary=True)
        _ensure_domain(org0.id, t0_local_domain, is_primary=False)
        rbac_service.seed_starter_roles(org0)

        # 2. Tenant 1: Apex Altitude Expeditions
        t1_prod_domain = f"tma.tenant1.{base_domain}"
        t1_local_domain = "tma.tenant1.localhost"

        org1 = Organization.query.filter(
            (Organization.name == "Apex Altitude Expeditions")
            | (Organization.name == "Apex Altitude")
            | (Organization.name == "Sahyadri Adventures")
        ).first()

        if not org1:
            org1 = Organization(
                name="Apex Altitude Expeditions",
                status="active",
                created_by_platform_admin_id=creator.id,
                theme="glacier_obsidian",
                primary_color="#00F5D4",
                secondary_color="#FF6B35",
                contact_email="dispatch@apex.info",
            )
            db.session.add(org1)
            db.session.flush()
        else:
            org1.status = "active"
            org1.theme = "glacier_obsidian"
            if not org1.created_by_platform_admin_id:
                org1.created_by_platform_admin_id = creator.id
            db.session.flush()

        _ensure_domain(org1.id, t1_prod_domain, is_primary=True)
        _ensure_domain(org1.id, t1_local_domain, is_primary=False)
        rbac_service.seed_starter_roles(org1)

        # 3. Deactivate all other tenants
        active_ids = {org0.id, org1.id}
        suspended_count = 0
        other_orgs = Organization.query.filter(~Organization.id.in_(active_ids)).all()
        for other in other_orgs:
            if other.status != "suspended":
                other.status = "suspended"
                suspended_count += 1

        db.session.commit()

        return {
            "tenant0": {"id": str(org0.id), "name": org0.name, "domain": t0_prod_domain},
            "tenant1": {"id": str(org1.id), "name": org1.name, "domain": t1_prod_domain},
            "suspended_count": suspended_count,
        }


def seed_recruiter_demo() -> Dict[str, Any]:
    """
    Idempotently seeds recruiter demo accounts, sample multi-day treks,
    bookings, and waitlist for evaluation.
    """
    from datetime import date, timedelta
    from werkzeug.security import generate_password_hash
    from app.models.trek import Trek
    from app.models.trek_itinerary_day import TrekItineraryDay
    from app.models.itinerary_element import ItineraryElement
    from app.models.waitlist_entry import WaitlistEntry
    from app.tenancy.scoping import tenant_context

    # First ensure live tenant subdomains exist
    summary = init_live_tenants()

    base_domain = (os.environ.get("BASE_DOMAIN") or "abhishekpathak.site").split(":")[0].strip().lower()

    with tenant_bypass():
        dom0 = Domain.query.filter_by(domain_name=f"tma.tenant0.{base_domain}").first()
        dom1 = Domain.query.filter_by(domain_name=f"tma.tenant1.{base_domain}").first()
        org0 = Organization.query.get(dom0.organization_id)
        org1 = Organization.query.get(dom1.organization_id)

        # Helper to seed users idempotently
        def _seed_user(org_id, email, name, role, password="Demo@123"):
            with tenant_context(org_id):
                user = User.query.filter_by(organization_id=org_id, email=email.lower().strip()).first()
                if not user:
                    user = User(
                        organization_id=org_id,
                        name=name,
                        email=email.lower().strip(),
                        password_hash=generate_password_hash(password),
                        role=role,
                        is_active=True,
                        mobile_country_code="+91",
                        mobile_number="9876543210",
                        terms_accepted_at=datetime.now(timezone.utc),
                    )
                    db.session.add(user)
                    db.session.flush()
                else:
                    user.password_hash = generate_password_hash(password)
                    user.is_active = True
                    user.terms_accepted_at = datetime.now(timezone.utc)
                    db.session.flush()
                return user

        # 1. Tenant 0 Admin & Trekker
        admin0 = _seed_user(org0.id, "admin@tenant0.com", "Tenant 0 Admin", "admin")
        trekker0 = _seed_user(org0.id, "trekker@tenant0.com", "Aarav Sharma", "trekker")
        trekker0_sample = _seed_user(org0.id, "trekker2@tenant0.com", "Neha Verma", "trekker")
        org0.tenant_admin_user_id = admin0.id
        db.session.flush()

        # 2. Tenant 1 Admin & Trekker
        admin1 = _seed_user(org1.id, "admin@tenant1.com", "Tenant 1 Admin", "admin")
        trekker1 = _seed_user(org1.id, "trekker@tenant1.com", "Rohan Kulkarni", "trekker")
        org1.tenant_admin_user_id = admin1.id
        db.session.flush()

        # 3. Seed Treks for Tenant 0 (2-3 Active Multi-Day Treks)
        with tenant_context(org0.id):
            # Trek 1: Roopkund Mystery Lake
            trek0_1 = Trek.query.filter_by(organization_id=org0.id, name="Roopkund Mystery Lake Expedition").first()
            if not trek0_1:
                trek0_1 = Trek(
                    organization_id=org0.id,
                    name="Roopkund Mystery Lake Expedition",
                    location="Uttarakhand",
                    difficulty="moderate",
                    duration_days=6,
                    total_slots=20,
                    available_slots=17,
                    price=12500.0,
                    status="open",
                    itinerary_layout="timeline",
                    start_date=date.today() + timedelta(days=20),
                    end_date=date.today() + timedelta(days=26),
                    description="Climb through rhododendron forests and high meadows of Ali Bugyal to the glacial lake of Roopkund at 15,750 ft.",
                )
                db.session.add(trek0_1)
                db.session.flush()

                # Add Days & Elements
                d1 = TrekItineraryDay(
                    organization_id=org0.id,
                    trek_id=trek0_1.id,
                    day_number=1,
                    title="Lohajung Basecamp Gathering",
                    description="Equipment inspection and safety briefing at basecamp.",
                    important_points=["Verify personal medical kit", "Collect microspikes and gaiters"],
                )
                db.session.add(d1)
                db.session.flush()

                el1 = ItineraryElement(
                    organization_id=org0.id,
                    itinerary_day_id=d1.id,
                    sequence=1,
                    element_type="activity",
                    title="Gear Briefing & Medical Check",
                    time_of_day="04:00 PM",
                    description="Full gear inspection with senior trek guides.",
                    points=["Headlamp testing", "SpO2 baseline check"],
                )
                db.session.add(el1)

            # Trek 2: Kedarkantha Winter Peak Summit
            trek0_2 = Trek.query.filter_by(organization_id=org0.id, name="Kedarkantha Winter Peak Summit").first()
            if not trek0_2:
                trek0_2 = Trek(
                    organization_id=org0.id,
                    name="Kedarkantha Winter Peak Summit",
                    location="Uttarakhand",
                    difficulty="easy",
                    duration_days=5,
                    total_slots=25,
                    available_slots=22,
                    price=8500.0,
                    status="open",
                    itinerary_layout="accordion",
                    start_date=date.today() + timedelta(days=12),
                    end_date=date.today() + timedelta(days=17),
                    description="A classic Himalayan snow trek offering a 360-degree summit panorama of Gangotri and Swargarohini peaks.",
                )
                db.session.add(trek0_2)
                db.session.flush()

                d1_k = TrekItineraryDay(
                    organization_id=org0.id,
                    trek_id=trek0_2.id,
                    day_number=1,
                    title="Sankri Basecamp Ingress",
                    description="Scenic drive from Dehradun through Mussoorie to Sankri.",
                    important_points=["Carry warm thermals", "Keep valid ID card ready"],
                )
                db.session.add(d1_k)
                db.session.flush()

            # Seed 2 Sample Confirmed Bookings + 1 Waitlisted Entry on Tenant 0
            b1 = Booking.query.filter_by(organization_id=org0.id, user_id=trekker0.id, trek_id=trek0_1.id).first()
            if not b1:
                b1 = Booking(
                    organization_id=org0.id,
                    trek_id=trek0_1.id,
                    user_id=trekker0.id,
                    slot_count=1,
                    total_price=12500.0,
                    status="booked",
                    payment_status="paid",
                )
                db.session.add(b1)

            b2 = Booking.query.filter_by(organization_id=org0.id, user_id=trekker0_sample.id, trek_id=trek0_2.id).first()
            if not b2:
                b2 = Booking(
                    organization_id=org0.id,
                    trek_id=trek0_2.id,
                    user_id=trekker0_sample.id,
                    slot_count=2,
                    total_price=17000.0,
                    status="booked",
                    payment_status="paid",
                )
                db.session.add(b2)

            wl = WaitlistEntry.query.filter_by(
                organization_id=org0.id, user_id=trekker0.id, trek_id=trek0_2.id
            ).first()
            if not wl:
                wl = WaitlistEntry(
                    organization_id=org0.id,
                    trek_id=trek0_2.id,
                    user_id=trekker0.id,
                    requested_slot_count=1,
                    status="waiting",
                )
                db.session.add(wl)

        # 4. Seed 2 Active Treks for Tenant 1 (Apex Altitude Expeditions)
        with tenant_context(org1.id):
            trek1_1 = Trek.query.filter_by(organization_id=org1.id, name="Chadar Frozen River Expedition").first()
            if not trek1_1:
                trek1_1 = Trek(
                    organization_id=org1.id,
                    name="Chadar Frozen River Expedition",
                    location="Zanskar, Ladakh",
                    difficulty="hard",
                    duration_days=8,
                    total_slots=12,
                    available_slots=9,
                    price=24500.0,
                    status="open",
                    itinerary_layout="magazine",
                    start_date=date.today() + timedelta(days=25),
                    end_date=date.today() + timedelta(days=33),
                    description="Traverse the frozen ice sheet of the Zanskar River beneath 600m sheer gorge walls in sub-zero winter temperatures.",
                )
                db.session.add(trek1_1)
                db.session.flush()

                c_d1 = TrekItineraryDay(
                    organization_id=org1.id,
                    trek_id=trek1_1.id,
                    day_number=1,
                    title="Leh High-Altitude Ingress",
                    description="Acclimatization day in Leh. Mandatory SpO2 medical clearance.",
                    important_points=["Complete rest for first 24 hours", "SpO2 must register above 85%"],
                )
                db.session.add(c_d1)

            trek1_2 = Trek.query.filter_by(organization_id=org1.id, name="Stok Kangri Alpine Trek").first()
            if not trek1_2:
                trek1_2 = Trek(
                    organization_id=org1.id,
                    name="Stok Kangri Alpine Trek",
                    location="Ladakh",
                    difficulty="hard",
                    duration_days=6,
                    total_slots=15,
                    available_slots=12,
                    price=18500.0,
                    status="open",
                    itinerary_layout="timeline",
                    start_date=date.today() + timedelta(days=18),
                    end_date=date.today() + timedelta(days=24),
                    description="An exhilarating high altitude adventure crossing Chang Ma and Mankorma basecamps.",
                )
                db.session.add(trek1_2)

        db.session.commit()

        return {
            "status": "success",
            "platform_admin": "superadmin@platform.com",
            "tenant0": summary["tenant0"],
            "tenant1": summary["tenant1"],
        }

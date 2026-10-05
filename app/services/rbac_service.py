"""
Role/capability logic + the protected-capability list (TDR v2 §4.2, §6; PDR v2 §5.3, §11).
Framework-agnostic.
"""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional, Set
import uuid

import sqlalchemy as sa
from sqlalchemy import func

from app.extensions import db
from app.models.capability import Capability
from app.models.organization import Organization
from app.models.role import Role, role_capabilities
from app.models.staff_profile import StaffProfile
from app.models.audit_log import AuditLog
from app.tenancy.scoping import tenant_bypass


# 1. Module constant PROTECTED_CAPABILITY_KEYS (code-level, NOT read from the DB)
PROTECTED_CAPABILITY_KEYS: Set[str] = {
    "settings.org.edit",
    "payments.payout.configure",
    "manual_booking.toggle",
    "manual_booking.sign_off",
    "roles.manage",
    "org.delete",
}

# 2. Module constant CAPABILITY_CATALOG (single source for the menu)
CAPABILITY_CATALOG: List[tuple[str, str, bool]] = [
    # Non-protected staff capabilities
    ("treks.manage_assigned", "Manage assigned treks, batches, and slot availability", False),
    ("participants.view", "View trekker rosters, participant medical notes, and emergency contacts", False),
    ("payments.view_assigned", "View payment and transaction statuses on assigned treks", False),
    ("tickets.view", "View incoming customer query and problem tickets", False),
    ("tickets.respond", "Respond to support tickets and change ticket status", False),
    ("reports.view", "View trek completion reports, analytics, and participant summaries", False),
    # Protected tenant-admin capabilities (for catalog listing/documentation only; enforcement NEVER reads DB)
    ("settings.org.edit", "Edit organization profile, branding, colors, and legal terms", True),
    ("payments.payout.configure", "Configure payout bank accounts and Razorpay Route linkage", True),
    ("manual_booking.toggle", "Enable or disable manual and offline booking intake", True),
    ("manual_booking.sign_off", "Review, sign off, and confirm manual bookings", True),
    ("roles.manage", "Create, edit, and delete staff roles and capability mappings", True),
    ("org.delete", "Initiate tenant deletion and full organization data purge", True),
]


class ProtectedCapabilityError(Exception):
    """Raised when attempting to assign or manipulate a protected capability on a role."""
    pass


class UnknownCapabilityError(Exception):
    """Raised when attempting to assign a capability not found in the global catalog."""
    pass


class RoleInUseError(Exception):
    """Raised when attempting to delete a role that is assigned to active staff."""
    pass


def seed_catalog() -> int:
    """
    Idempotent upsert of the global capability catalog.
    Never deletes existing rows.
    """
    count = 0
    with tenant_bypass():
        for key, description, is_protected in CAPABILITY_CATALOG:
            cap = Capability.query.filter_by(key=key).first()
            if not cap:
                cap = Capability(
                    key=key,
                    description=description,
                    is_protected=is_protected,
                )
                db.session.add(cap)
                count += 1
            else:
                cap.description = description
                cap.is_protected = is_protected
        db.session.commit()
    return count


def seed_starter_roles(org: Organization) -> List[Role]:
    """
    Creates starter template roles for the given organization:
    - Finance: payments.view_assigned, reports.view
    - Support: tickets.view, tickets.respond, participants.view
    - Trek Coordinator: all six non-protected capabilities
    Idempotent per (org.id, name).
    """
    starter_definitions = [
        (
            "Finance",
            ["payments.view_assigned", "reports.view"],
        ),
        (
            "Support",
            ["tickets.view", "tickets.respond", "participants.view"],
        ),
        (
            "Trek Coordinator",
            [
                "treks.manage_assigned",
                "participants.view",
                "payments.view_assigned",
                "tickets.view",
                "tickets.respond",
                "reports.view",
            ],
        ),
    ]

    roles_created = []
    with tenant_bypass():
        for role_name, cap_keys in starter_definitions:
            existing = Role.query.filter_by(organization_id=org.id, name=role_name).first()
            if not existing:
                caps = Capability.query.filter(Capability.key.in_(cap_keys)).all()
                role = Role(
                    organization_id=org.id,
                    name=role_name,
                    is_starter_template=True,
                )
                role.capabilities = caps
                db.session.add(role)
                roles_created.append(role)
        db.session.commit()
    return roles_created


def create_role(
    actor: Any,
    org: Organization,
    name: str,
    capability_keys: List[str],
) -> Role:
    """
    Creates a new custom role within the organization.
    Rejects any protected capability keys against PROTECTED_CAPABILITY_KEYS.
    Writes AuditLog entries for creation and each capability grant.
    Does NOT commit — caller manages transaction.
    """
    role_name = (name or "").strip()
    if not role_name:
        raise ValueError("Role name cannot be empty.")

    # Check for duplicate name in org
    existing = Role.query.filter_by(organization_id=org.id, name=role_name).first()
    if existing:
        raise ValueError(f"A role named '{role_name}' already exists in this organization.")

    # Verify protected keys against CODE constant
    for k in capability_keys:
        if k in PROTECTED_CAPABILITY_KEYS:
            raise ProtectedCapabilityError(
                f"Capability '{k}' is protected and cannot be assigned to roles."
            )

    # Resolve capability models
    caps = []
    if capability_keys:
        caps = Capability.query.filter(Capability.key.in_(capability_keys)).all()
        found_keys = {c.key for c in caps}
        missing = set(capability_keys) - found_keys
        if missing:
            raise UnknownCapabilityError(f"Unknown capabilities: {', '.join(sorted(missing))}")

    role = Role(
        organization_id=org.id,
        name=role_name,
        is_starter_template=False,
    )
    role.capabilities = caps
    db.session.add(role)

    # Audit logging
    log_create = AuditLog.record(
        actor_id=actor.id,
        action="role.create",
        target_type="role",
        target_id=0,
        details=json.dumps({"name": role_name, "capabilities": capability_keys}),
    )
    log_create.organization_id = org.id
    db.session.add(log_create)

    for k in capability_keys:
        log_grant = AuditLog.record(
            actor_id=actor.id,
            action="role.capability_grant",
            target_type="role",
            target_id=0,
            details=json.dumps({"role_name": role_name, "capability": k}),
        )
        log_grant.organization_id = org.id
        db.session.add(log_grant)

    # print(f"[DEBUG] rbac role={role.name} keys={sorted(capability_keys)}")
    return role


def update_role(
    actor: Any,
    org: Organization,
    role: Role,
    name: str,
    capability_keys: List[str],
) -> Role:
    """
    Updates role name and capability list.
    Rejects protected keys.
    Audits role.update as well as role.capability_grant and role.capability_revoke diffs.
    Does NOT commit.
    """
    if role.organization_id != org.id:
        raise ValueError("Role does not belong to this organization.")

    role_name = (name or "").strip()
    if not role_name:
        raise ValueError("Role name cannot be empty.")

    if role_name != role.name:
        existing = Role.query.filter_by(organization_id=org.id, name=role_name).first()
        if existing and existing.id != role.id:
            raise ValueError(f"A role named '{role_name}' already exists in this organization.")

    # Validate against protected constant
    for k in capability_keys:
        if k in PROTECTED_CAPABILITY_KEYS:
            raise ProtectedCapabilityError(
                f"Capability '{k}' is protected and cannot be assigned to roles."
            )

    caps = []
    if capability_keys:
        caps = Capability.query.filter(Capability.key.in_(capability_keys)).all()
        found_keys = {c.key for c in caps}
        missing = set(capability_keys) - found_keys
        if missing:
            raise UnknownCapabilityError(f"Unknown capabilities: {', '.join(sorted(missing))}")

    old_keys = {c.key for c in role.capabilities}
    new_keys = set(capability_keys)

    added = new_keys - old_keys
    removed = old_keys - new_keys

    role.name = role_name
    role.capabilities = caps

    log_update = AuditLog.record(
        actor_id=actor.id,
        action="role.update",
        target_type="role",
        target_id=0,
        details=json.dumps({
            "name": role_name,
            "added": sorted(added),
            "removed": sorted(removed),
        }),
    )
    log_update.organization_id = org.id
    db.session.add(log_update)

    for k in sorted(added):
        grant_log = AuditLog.record(
            actor_id=actor.id,
            action="role.capability_grant",
            target_type="role",
            target_id=0,
            details=json.dumps({"role_name": role_name, "capability": k}),
        )
        grant_log.organization_id = org.id
        db.session.add(grant_log)

    for k in sorted(removed):
        revoke_log = AuditLog.record(
            actor_id=actor.id,
            action="role.capability_revoke",
            target_type="role",
            target_id=0,
            details=json.dumps({"role_name": role_name, "capability": k}),
        )
        revoke_log.organization_id = org.id
        db.session.add(revoke_log)

    # print(f"[DEBUG] rbac role={role.name} keys={sorted(capability_keys)}")
    return role


def delete_role(actor: Any, org: Organization, role: Role) -> None:
    """
    Deletes a custom role. Rejected while any StaffProfile holds the role.
    Does NOT commit.
    """
    if role.organization_id != org.id:
        raise ValueError("Role does not belong to this organization.")

    # Check if assigned to any staff profile
    in_use = StaffProfile.query.filter_by(role_id=role.id).first()
    if in_use:
        raise RoleInUseError("Cannot delete role while staff members are assigned to it.")

    log_del = AuditLog.record(
        actor_id=actor.id,
        action="role.delete",
        target_type="role",
        target_id=0,
        details=json.dumps({"role_id": str(role.id), "name": role.name}),
    )
    log_del.organization_id = org.id
    db.session.add(log_del)

    db.session.delete(role)


def assign_role(actor: Any, staff_profile: StaffProfile, role: Role) -> StaffProfile:
    """
    Assigns a role to a staff member.
    The role and staff member must belong to the exact same organization.
    Audits role.assign. Does NOT commit.
    """
    if staff_profile.organization_id != role.organization_id:
        raise ValueError("Cannot assign a role from another organization.")

    staff_profile.role_id = role.id

    log_assign = AuditLog.record(
        actor_id=actor.id,
        action="role.assign",
        target_type="staff_profile",
        target_id=staff_profile.id,
        details=json.dumps({"role_id": str(role.id), "role_name": role.name}),
    )
    log_assign.organization_id = staff_profile.organization_id
    db.session.add(log_assign)

    return staff_profile


def get_capability_keys(user: Any) -> List[str]:
    """
    Returns list of capability keys for the user at login time for session snapshot.
    For staff users, returns keys of their assigned role (empty list if no role).
    Empty list for any other role.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return []

    if getattr(user, "role", None) != "staff":
        return []

    staff_prof = getattr(user, "staff_profile", None)
    if not staff_prof or not staff_prof.role:
        return []

    return [c.key for c in staff_prof.role.capabilities]


def backfill_staff_roles() -> int:
    """
    For every organization, approved staff with role_id NULL get the 'Trek Coordinator' role.
    Writes AuditLog entries with details 'legacy backfill'.
    Idempotent.
    """
    backfill_count = 0
    with tenant_bypass():
        orgs = Organization.query.all()
        for org in orgs:
            # Find or seed Trek Coordinator role
            trek_coord = Role.query.filter_by(
                organization_id=org.id,
                name="Trek Coordinator",
            ).first()

            if not trek_coord:
                seed_starter_roles(org)
                trek_coord = Role.query.filter_by(
                    organization_id=org.id,
                    name="Trek Coordinator",
                ).first()

            if not trek_coord:
                continue

            staff_list = StaffProfile.query.filter_by(
                organization_id=org.id,
                approval_status="approved",
                role_id=None,
            ).all()

            for staff in staff_list:
                staff.role_id = trek_coord.id
                backfill_count += 1

                # Actor is tenant admin if available, or staff's user_id
                actor_id = org.tenant_admin_user_id or staff.user_id
                log_entry = AuditLog.record(
                    actor_id=actor_id,
                    action="role.assign",
                    target_type="staff_profile",
                    target_id=staff.id,
                    details="legacy backfill",
                )
                log_entry.organization_id = org.id
                db.session.add(log_entry)

        db.session.commit()
    return backfill_count

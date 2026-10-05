"""
Tenant Admin "Roles & Capabilities" management and staff role assignment
(UI/UX v2 §3.3; PDR v2 §14.1 /admin/roles, /admin/staff/<id>/role).
Role management is itself a PROTECTED capability.
"""

from typing import Any
import uuid

from flask import abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.admin import admin_bp
from app.auth.decorators import protected_capability_required, role_required
from app.extensions import db
from app.models.role import Role
from app.models.staff_profile import StaffProfile
from app.services import rbac_service
from app.services.rbac_service import (
    ProtectedCapabilityError,
    RoleInUseError,
    UnknownCapabilityError,
)


@admin_bp.route("/roles", methods=["GET"])
@login_required
@role_required("admin")
@protected_capability_required
def roles_list() -> Any:
    """Lists all roles defined within the active tenant."""
    # print(f"[DEBUG] roles route {request.path} user={current_user.id}")
    roles = Role.query.filter_by(organization_id=g.organization_id).order_by(Role.created_at.asc()).all()

    roles_data = []
    for r in roles:
        staff_count = StaffProfile.query.filter_by(role_id=r.id).count()
        roles_data.append({
            "role": r,
            "capabilities_count": len(r.capabilities),
            "staff_count": staff_count,
        })

    return render_template("admin/roles_list.html", roles=roles_data)


@admin_bp.route("/roles/new", methods=["GET", "POST"])
@login_required
@role_required("admin")
@protected_capability_required
def role_new() -> Any:
    """Creates a new custom role with selected non-protected capabilities."""
    # print(f"[DEBUG] roles route {request.path} user={current_user.id}")
    catalog = [item for item in rbac_service.CAPABILITY_CATALOG if not item[2]]

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        capability_keys = request.form.getlist("capabilities")

        try:
            rbac_service.create_role(
                actor=current_user,
                org=g.organization,
                name=name,
                capability_keys=capability_keys,
            )
            db.session.commit()
            flash("Role created successfully. Permissions apply to staff at their next login.", "success")
            return redirect(url_for("admin.roles_list"))
        except ProtectedCapabilityError as err:
            db.session.rollback()
            flash(str(err), "danger")
            return render_template("admin/role_form.html", role=None, catalog=catalog, selected_keys=capability_keys, name=name), 422
        except (ValueError, UnknownCapabilityError) as err:
            db.session.rollback()
            flash(str(err), "danger")
            return render_template("admin/role_form.html", role=None, catalog=catalog, selected_keys=capability_keys, name=name), 400

    return render_template("admin/role_form.html", role=None, catalog=catalog, selected_keys=[], name="")


@admin_bp.route("/roles/<uuid:id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
@protected_capability_required
def role_edit(id: uuid.UUID) -> Any:
    """Edits an existing role's name and capabilities."""
    # print(f"[DEBUG] roles route {request.path} user={current_user.id}")
    role = Role.query.filter_by(id=id, organization_id=g.organization_id).first_or_404()
    catalog = [item for item in rbac_service.CAPABILITY_CATALOG if not item[2]]
    current_keys = [c.key for c in role.capabilities]

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        capability_keys = request.form.getlist("capabilities")

        try:
            rbac_service.update_role(
                actor=current_user,
                org=g.organization,
                role=role,
                name=name,
                capability_keys=capability_keys,
            )
            db.session.commit()
            flash("Role updated successfully. Permissions apply to staff at their next login.", "success")
            return redirect(url_for("admin.roles_list"))
        except ProtectedCapabilityError as err:
            db.session.rollback()
            flash(str(err), "danger")
            return render_template("admin/role_form.html", role=role, catalog=catalog, selected_keys=capability_keys, name=name), 422
        except (ValueError, UnknownCapabilityError) as err:
            db.session.rollback()
            flash(str(err), "danger")
            return render_template("admin/role_form.html", role=role, catalog=catalog, selected_keys=capability_keys, name=name), 400

    return render_template("admin/role_form.html", role=role, catalog=catalog, selected_keys=current_keys, name=role.name)


@admin_bp.route("/roles/<uuid:id>/delete", methods=["POST"])
@login_required
@role_required("admin")
@protected_capability_required
def role_delete(id: uuid.UUID) -> Any:
    """Deletes an unused role."""
    # print(f"[DEBUG] roles route {request.path} user={current_user.id}")
    role = Role.query.filter_by(id=id, organization_id=g.organization_id).first_or_404()
    try:
        rbac_service.delete_role(actor=current_user, org=g.organization, role=role)
        db.session.commit()
        flash(f"Role '{role.name}' was successfully deleted.", "success")
    except RoleInUseError as err:
        db.session.rollback()
        flash(str(err), "danger")
    except Exception as err:
        db.session.rollback()
        flash(f"Error deleting role: {err}", "danger")

    return redirect(url_for("admin.roles_list"))


@admin_bp.route("/staff/<hashid:id>/role", methods=["POST"])
@login_required
@role_required("admin")
@protected_capability_required
def staff_assign_role(id: int) -> Any:
    """Assigns a role to an approved staff profile."""
    # print(f"[DEBUG] roles route {request.path} user={current_user.id}")
    staff_profile = StaffProfile.query.filter_by(id=id, organization_id=g.organization_id).first_or_404()

    role_id_str = request.form.get("role_id", "").strip()
    if not role_id_str:
        flash("Please select a role to assign.", "danger")
        return redirect(url_for("admin.staff_detail", staff_id=id))

    try:
        role_uuid = uuid.UUID(role_id_str)
    except (ValueError, TypeError):
        flash("Invalid role identifier provided.", "danger")
        return redirect(url_for("admin.staff_detail", staff_id=id))

    role = Role.query.filter_by(id=role_uuid, organization_id=g.organization_id).first()
    if not role:
        flash("The selected role does not belong to this organization.", "danger")
        return redirect(url_for("admin.staff_detail", staff_id=id))

    try:
        rbac_service.assign_role(actor=current_user, staff_profile=staff_profile, role=role)
        db.session.commit()
        flash(f"Role '{role.name}' assigned to {staff_profile.user.name}. Changes will apply at their next login.", "success")
    except Exception as err:
        db.session.rollback()
        flash(f"Failed to assign role: {err}", "danger")

    return redirect(url_for("admin.staff_detail", staff_id=id))

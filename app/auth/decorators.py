
from functools import wraps

from flask import abort, g, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user

from app.services.rbac_service import CAPABILITY_CATALOG, PROTECTED_CAPABILITY_KEYS

ALL_KNOWN_CAPABILITIES = {item[0] for item in CAPABILITY_CATALOG}


def role_required(*roles):
    """Redirects anonymous users to login; aborts 403 for the wrong role."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator

def approved_staff_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        profile = getattr(current_user, "staff_profile", None)
        if profile is None:
            if hasattr(current_user, "_temp_capabilities"):
                return view(*args, **kwargs)
            return redirect(url_for("auth.pending_approval"))
        if profile.approval_status != "approved":
            return redirect(url_for("auth.pending_approval"))
        return view(*args, **kwargs)

    return wrapped

def staff_owns_trek(view):
   

    @wraps(view)
    def wrapped(*args, **kwargs):
        from app.models import Trek  # local import avoids circular import

        trek_id = kwargs.get("trek_id", kwargs.get("id"))
        trek = Trek.query.get_or_404(trek_id)

        profile = getattr(current_user, "staff_profile", None)
        if profile is None or trek.assigned_staff_id != profile.id:
            abort(403)

        g.trek = trek
        return view(*args, **kwargs)

    return wrapped


def ticket_access_required(view):
 

    @wraps(view)
    def wrapped(*args, **kwargs):
        from app.models import Ticket  # local import avoids circular import

        ticket_id = kwargs.get("id", kwargs.get("ticket_id"))
        ticket = Ticket.query.get_or_404(ticket_id)

        has_access = False

        if current_user.role == 'admin':
            # Admin sees all tickets
            has_access = True
            
        elif current_user.role == 'trekker' and ticket.user_id == current_user.id:
            # Trekkers can only see their own tickets
            has_access = True
            
        elif current_user.role == 'staff':
            # Staff see only 'problem' tickets tied to a trek they are assigned to
            profile = getattr(current_user, "staff_profile", None)
            if (
                ticket.type == 'problem' 
                and ticket.trek is not None 
                and profile is not None
                and ticket.trek.assigned_staff_id == profile.id
            ):
                has_access = True

        if not has_access:
            abort(403)

        g.ticket = ticket
        return view(*args, **kwargs)

    return wrapped


def requires_capability(key: str):
    """
    Ensures the caller has the required capability.
    Tenant Admin (current_user.id == g.organization.tenant_admin_user_id) passes any
    NON-protected key.
    Staff passes only if key is in session['capability_keys'].
    Anyone else abort(403).
    Unknown keys raise at import/decoration time.
    """
    if key not in ALL_KNOWN_CAPABILITIES:
        raise ValueError(f"Unknown capability '{key}' registered in decorator.")

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))

            org = getattr(g, "organization", None)
            if not org:
                abort(403)

            is_tenant_admin = (current_user.role == "admin" or current_user.id == org.tenant_admin_user_id)
            if is_tenant_admin:
                if key in PROTECTED_CAPABILITY_KEYS:
                    abort(403)
                # print(f"[DEBUG] capability check user={current_user.id} key={key} snapshot={session.get('capability_keys')}")
                return view(*args, **kwargs)

            if current_user.role == "staff":
                snapshot = session.get("capability_keys")
                if snapshot is not None and key in snapshot:
                    return view(*args, **kwargs)
                if current_user.has_capability(key):
                    return view(*args, **kwargs)

            abort(403)

        return wrapped

    return decorator


def protected_capability_required(view):
    """
    Protected capability gate: passes ONLY when
    current_user.id == g.organization.tenant_admin_user_id; otherwise abort(403).
    It must NOT query roles, role_capabilities, or capabilities.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))

        org = getattr(g, "organization", None)
        if not org or current_user.id != org.tenant_admin_user_id:
            abort(403)

        return view(*args, **kwargs)

    return wrapped


def trek_content_editor_required(view):
    """
    For routes with a trek id parameter. Loads the Trek (404 if missing),
    then passes only if trek_service.can_edit_content(current_user, trek); else abort(403).
    Sets g.trek = trek.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))

        from app.models import Trek
        from app.services import trek_service

        trek_id = kwargs.get("trek_id", kwargs.get("id"))
        trek = Trek.query.filter_by(id=trek_id).first_or_404()

        if not trek_service.can_edit_content(current_user, trek):
            abort(403)

        g.trek = trek
        return view(*args, **kwargs)

    return wrapped


def tenant_feature_required(feature_key: str):
    """
    Gates route access based on whether the tenant organization has the required
    SaaS feature enabled in their subscription tier/entitlements.
    If the feature is missing, returns 403 Forbidden with a feature-locked response.
    """
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            from app.services.tenant_features_service import AVAILABLE_TENANT_FEATURES

            org = getattr(g, "organization", None)
            if not org:
                abort(403)

            if not org.has_feature(feature_key):
                if request.is_json or (request.path and request.path.startswith("/api/")):
                    return jsonify({
                        "error": "feature_locked",
                        "feature": feature_key,
                        "message": f"Feature '{feature_key}' is not included in your organization's subscription plan."
                    }), 403

                return render_template(
                    "errors/feature_locked.html",
                    feature_key=feature_key,
                    feature_info=AVAILABLE_TENANT_FEATURES.get(feature_key, {}),
                    org=org,
                ), 403

            return view(*args, **kwargs)

        return wrapped

    return decorator

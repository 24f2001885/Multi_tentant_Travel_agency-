"""
Platform Admin's own login path and session — entirely separate from Flask-Login
tenant auth (TDR v2 §2, §6).
"""

from functools import wraps
from typing import Callable, Optional
import uuid

from flask import current_app, g, redirect, session, url_for

from app.models.platform_admin import PlatformAdmin
from app.tenancy.scoping import tenant_bypass


def current_platform_admin() -> Optional[PlatformAdmin]:
    """
    Returns the currently authenticated PlatformAdmin for the request, or None.
    Caches the lookup on Flask `g.current_platform_admin`.
    """
    if hasattr(g, "current_platform_admin"):
        return g.current_platform_admin

    admin_id_str = session.get("platform_admin_id")
    # print(f"[DEBUG] platform auth admin_id={admin_id_str}")
    if not admin_id_str:
        g.current_platform_admin = None
        return None

    try:
        admin_uuid = uuid.UUID(admin_id_str)
    except (ValueError, TypeError):
        session.pop("platform_admin_id", None)
        g.current_platform_admin = None
        return None

    with tenant_bypass():
        admin = PlatformAdmin.query.get(admin_uuid)

    if not admin or not admin.is_active:
        session.pop("platform_admin_id", None)
        g.current_platform_admin = None
        return None

    g.current_platform_admin = admin
    return admin


def platform_login(email: str, password: str) -> Optional[PlatformAdmin]:
    """
    Authenticates a Platform Admin and records their ID into session['platform_admin_id'].
    Returns PlatformAdmin on success, or None on failure.
    NEVER invokes flask_login.login_user.
    """
    norm_email = (email or "").strip().lower()
    if not norm_email or not password:
        return None

    with tenant_bypass():
        admin = PlatformAdmin.query.filter_by(email=norm_email).first()

    if not admin or not admin.is_active:
        return None

    if not admin.check_password(password):
        return None

    session["platform_admin_id"] = str(admin.id)
    g.current_platform_admin = admin
    return admin


def platform_logout() -> None:
    """Terminates the Platform Admin session."""
    session.pop("platform_admin_id", None)
    if hasattr(g, "current_platform_admin"):
        g.current_platform_admin = None


def platform_admin_required(fn: Callable) -> Callable:
    """
    Route decorator ensuring a valid Platform Admin session.
    Redirects to platform.login if unauthenticated.
    Never stacks with tenant-aware decorators.
    """
    @wraps(fn)
    def decorated_view(*args, **kwargs):
        admin = current_platform_admin()
        if not admin:
            return redirect(url_for("platform.login"))
        return fn(*args, **kwargs)

    return decorated_view

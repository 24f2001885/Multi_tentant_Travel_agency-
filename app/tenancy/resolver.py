"""
Tenant resolution module for the TMA Multi-Tenant Platform (TDR v2 §2).

Resolves the active tenant on each incoming request from the HTTP Host header,
mapping it to an Organization via the `domains` table.
"""

import os
from typing import Tuple

from flask import Flask, abort, g, render_template, request

from app.tenancy.scoping import tenant_bypass

EXEMPT_PATHS: Tuple[str, ...] = (
    "/api/payments/webhook",
    "/api/payments/route-webhook",
)


def resolve_tenant():
    """
    before_request hook to resolve tenant from request.host.
    Sets g.organization_id and g.organization on success.
    """
    # 1. Skip resolution for static assets
    if request.endpoint == "static" or request.path.startswith("/static/"):
        return

    # 2. Normalize host: lowercase and strip port
    host = request.host.split(":")[0].strip().lower()

    # 3. Platform boundary enforcement (P8)
    platform_admin_host = os.environ.get("PLATFORM_ADMIN_HOST", "platform.localhost").split(":")[0].strip().lower()
    if host == platform_admin_host:
        # print(f"[DEBUG] boundary host={host} path={request.path}")
        if request.path.startswith("/platform") or request.path.startswith("/static"):
            return
        abort(404)

    # Tenant host: /platform/* routes return 404
    if request.path.startswith("/platform"):
        # print(f"[DEBUG] boundary host={host} path={request.path}")
        abort(404)

    # 4. Skip resolution for exempt paths (payment webhooks)
    if request.path in EXEMPT_PATHS:
        return

    # 5. Look up domain inside tenant_bypass
    from app.models.domain import Domain

    with tenant_bypass():
        domain = Domain.query.filter_by(domain_name=host).first()

        # 6. Validate routability
        if not domain or not domain.is_routable:
            abort(404)

        org = domain.organization
        if not org or org.status != "active":
            return (
                render_template(
                    "errors/generic.html",
                    code=403,
                    message="This organization is currently unavailable.",
                ),
                403,
            )

        # 7. Set tenant context on flask.g
        g.organization_id = org.id
        g.organization = org
        if hasattr(g, "_login_user"):
            del g._login_user
    # print(f"[DEBUG] tenant resolve host={host} -> org={getattr(g,'organization_id',None)}")


def init_tenancy(app: Flask) -> None:
    """Registers tenancy hooks with the Flask application."""
    app.before_request(resolve_tenant)

"""
Tenant resolution module for the TMA Multi-Tenant Platform (TDR v2 §2).

Resolves the active tenant on each incoming request from the HTTP Host header,
mapping it to an Organization via the `domains` table.
"""

import os
from typing import Tuple

from flask import Flask, abort, g, redirect, render_template, request

from app.extensions import db
from app.tenancy.scoping import tenant_bypass

EXEMPT_PATHS: Tuple[str, ...] = (
    "/webhook",
    "/api/payments/webhook",
    "/api/payments/route-webhook",
    "/api/whatsapp/webhook",
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

    # Friendly redirect for bare base domain / localhost / 127.0.0.1 development access
    base_domain = os.environ.get("BASE_DOMAIN", "abhishek4312.info").split(":")[0].strip().lower()
    platform_admin_host = os.environ.get("PLATFORM_ADMIN_HOST", f"platform.{base_domain}").split(":")[0].strip().lower()

    if host in (base_domain, "localhost", "127.0.0.1"):
        port = f":{request.host.split(':')[1]}" if ":" in request.host else ""
        query_suffix = f"?{request.query_string.decode('utf-8')}" if request.query_string else ""
        if request.path.startswith("/platform"):
            target_platform = "platform.localhost" if host == "localhost" else platform_admin_host
            return redirect(f"{request.scheme}://{target_platform}{port}{request.path}{query_suffix}", 302)

        # Check if incoming OAuth callback carries originating tenant host in state parameter
        state_param = request.args.get("state", "")
        if "__h_" in state_param:
            origin_host = state_param.split("__h_", 1)[1].split("__")[0].strip().lower()
            if origin_host and ("localhost" in origin_host or "." in origin_host):
                return redirect(f"{request.scheme}://{origin_host}{request.path}{query_suffix}", 302)

        tenant_zero_subdomain = os.environ.get("TENANT_ZERO_SUBDOMAIN", "tma.tenant0").strip().lower()
        target_domain_base = "localhost" if host == "localhost" else base_domain
        target_domain = (
            f"{tenant_zero_subdomain}.{target_domain_base}"
            if not tenant_zero_subdomain.endswith(target_domain_base)
            else tenant_zero_subdomain
        )
        return redirect(f"{request.scheme}://{target_domain}{port}{request.path}{query_suffix}", 302)

    # Skip resolution for exempt paths (payment and WhatsApp webhooks)
    if (
        request.path in EXEMPT_PATHS
        or request.path.startswith("/api/payments/webhook")
        or request.path.startswith("/api/payments/route-webhook")
        or request.path.startswith("/api/whatsapp/")
    ):
        return

    # 3. Platform boundary enforcement (P8)
    if host == platform_admin_host or (
        platform_admin_host != "platform.localhost" and host == "platform.localhost"
    ):
        # print(f"[DEBUG] boundary host={host} path={request.path}")
        allowed_prefixes = (
            "/platform",
            "/static",
            "/monitoring",
            "/organizations",
            "/backups",
            "/audit-logs",
            "/domains",
        )
        if any(request.path.startswith(p) for p in allowed_prefixes):
            return
        abort(404)

    # Tenant host: /platform/* routes and platform control plane routes return 404
    if any(
        request.path.startswith(p)
        for p in ("/platform", "/organizations", "/monitoring", "/backups", "/audit-logs", "/domains")
    ):
        # print(f"[DEBUG] boundary host={host} path={request.path}")
        abort(404)

    # 5. Look up domain inside tenant_bypass
    from app.models.domain import Domain

    with tenant_bypass():
        domain = Domain.query.filter_by(domain_name=host).first()

        # Development/local fallback: allows tma.tenant0.localhost to resolve if stored as tma.tenant0.abhishek4312.info
        if not domain and host.endswith(".localhost"):
            sub_prefix = host[:-len(".localhost")]
            domain = Domain.query.filter_by(domain_name=f"{sub_prefix}.{base_domain}").first()

        # Reverse fallback: allows production host to resolve if stored as .localhost in local environment
        if not domain and host.endswith(f".{base_domain}"):
            sub_prefix = host[:-len(f".{base_domain}")]
            domain = Domain.query.filter_by(domain_name=f"{sub_prefix}.localhost").first()

        # Development/tunnel fallback (ngrok, localtunnel, cloudflare tunnel, etc.)
        if not domain and (
            "ngrok" in host
            or host.endswith(".loca.lt")
            or host.endswith(".trycloudflare.com")
        ):
            tenant_zero_subdomain = os.environ.get("TENANT_ZERO_SUBDOMAIN", "tma.tenant0").strip().lower()
            target_default_domain = (
                f"{tenant_zero_subdomain}.{base_domain}"
                if not tenant_zero_subdomain.endswith(base_domain)
                else tenant_zero_subdomain
            )
            domain = Domain.query.filter_by(domain_name=target_default_domain).first() or Domain.query.filter_by(domain_type="subdomain").first()

        # 6. Validate routability
        if not domain or not domain.is_routable:
            abort(404)

        org = domain.organization
        if org:
            db.session.refresh(org)
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

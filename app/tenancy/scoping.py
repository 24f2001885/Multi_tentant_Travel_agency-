"""
Tenant scoping module for the TMA Multi-Tenant Platform (TDR v2 §6).

This module provides TenantScopedMixin and the SQLAlchemy session event listeners
that structurally enforce tenant isolation across all tenant-owned models.

Blind spots / notes:
1. Raw SQL statements executed via `db.session.execute(text(...))` or Core table
   selects are NOT automatically filtered by loader criteria. Any raw SQL queries
   must be scoped manually by organization_id.
2. Unscoped models that intentionally do NOT inherit TenantScopedMixin:
   - Organization (the tenant itself)
   - Domain (subdomain / custom domain routing)
   - PlatformAdmin (platform operator credentials, P8)
   - PlatformAccessLog (meta-audit log, P8)
   - Capability (shared system capability menu, P8)
"""

import contextvars
from contextlib import contextmanager
from typing import Any, Optional
import uuid

from flask import g, has_app_context, has_request_context
from sqlalchemy import event, inspect
from sqlalchemy.orm import declared_attr, with_loader_criteria
import sqlalchemy as sa

from app.extensions import db


class TenantContextMissingError(RuntimeError):
    """Raised when an ORM operation on a tenant-scoped model occurs without a tenant context or bypass."""
    pass


class TenantMismatchError(ValueError):
    """Raised when a tenant-scoped object's organization_id does not match the active tenant context."""
    pass


_bypass_var: contextvars.ContextVar[int] = contextvars.ContextVar("tenant_bypass_depth", default=0)
_tenant_var: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar("tenant_context_org_id", default=None)


def _is_bypass_active() -> bool:
    """Returns True if tenant bypass is currently active."""
    if has_request_context() or has_app_context():
        if getattr(g, "_tenant_bypass_depth", 0) > 0:
            return True
    return _bypass_var.get() > 0


def _get_current_org_id() -> Optional[Any]:
    """Retrieves the active organization_id from Flask g or contextvar."""
    if has_request_context() or has_app_context():
        if hasattr(g, "organization_id") and g.organization_id is not None:
            return g.organization_id
    return _tenant_var.get()


@contextmanager
def tenant_context(org_id: Any):
    """
    Context manager to temporarily set the active tenant organization_id.
    Nesting-safe across app/request contexts and background tasks.
    """
    prev_g_org = getattr(g, "organization_id", None) if (has_request_context() or has_app_context()) else None
    token = _tenant_var.set(org_id)
    if has_request_context() or has_app_context():
        g.organization_id = org_id

    try:
        yield
    finally:
        _tenant_var.reset(token)
        if has_request_context() or has_app_context():
            if prev_g_org is not None:
                g.organization_id = prev_g_org
            elif hasattr(g, "organization_id"):
                del g.organization_id


@contextmanager
def tenant_bypass():
    """
    Context manager to temporarily bypass tenant scoping.
    Nesting-safe. Authorized callers only:
    - resolver.py (domain lookup)
    - payments_resource.py (webhook order derivation)
    - scheduler maintenance jobs (stale booking expiry, ticket purge)
    - CLI commands (seed-tenant-zero, migrations)
    - platform admin control plane (P8)
    """
    current_depth = _bypass_var.get()
    token = _bypass_var.set(current_depth + 1)
    prev_g_depth = getattr(g, "_tenant_bypass_depth", 0) if (has_request_context() or has_app_context()) else 0
    if has_request_context() or has_app_context():
        g._tenant_bypass_depth = prev_g_depth + 1

    try:
        yield
    finally:
        _bypass_var.reset(token)
        if has_request_context() or has_app_context():
            if prev_g_depth > 0:
                g._tenant_bypass_depth = prev_g_depth
            elif hasattr(g, "_tenant_bypass_depth"):
                del g._tenant_bypass_depth


class TenantScopedMixin:
    """
    Mixin for tenant-owned models. Adds an indexed organization_id foreign key
    and participates in the global loader criteria filter.
    """

    @declared_attr
    def organization_id(cls):
        return db.Column(
            sa.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )


def _entity_is_scoped(mapper_or_class: Any) -> bool:
    """Checks if a mapper or class represents a TenantScopedMixin model."""
    try:
        cls = mapper_or_class.class_ if hasattr(mapper_or_class, "class_") else mapper_or_class
        return isinstance(cls, type) and issubclass(cls, TenantScopedMixin)
    except Exception:
        return False


@event.listens_for(db.session, "do_orm_execute")
def _receive_do_orm_execute(orm_execute_state):
    """
    SQLAlchemy hook applying automatic organization_id loader criteria
    to every ORM query unless tenant_bypass() is active.
    """
    if _is_bypass_active():
        # print(f"[DEBUG] scoping active org={getattr(g,'organization_id',None)} bypass=True")
        return

    current_org = _get_current_org_id()

    # Check if query targets or touches a tenant-scoped model
    has_scoped_model = False
    if orm_execute_state.all_mappers:
        for m in orm_execute_state.all_mappers:
            if _entity_is_scoped(m):
                has_scoped_model = True
                break

    if current_org is None:
        if has_scoped_model or orm_execute_state.is_relationship_load:
            raise TenantContextMissingError(
                "Tenant context is missing (g.organization_id is unset) while executing query on tenant-scoped model."
            )
        return

    # print(f"[DEBUG] scoping active org={current_org} bypass=False")
    orm_execute_state.statement = orm_execute_state.statement.options(
        with_loader_criteria(
            TenantScopedMixin,
            lambda cls: cls.organization_id == current_org,
            include_aliases=True,
        )
    )


@event.listens_for(db.session, "before_flush")
def _receive_before_flush(session, flush_context, instances):
    """
    Validates and auto-populates organization_id on newly instantiated objects
    before committing to the database.
    """
    if _is_bypass_active():
        return

    current_org = _get_current_org_id()

    for obj in session.new:
        if isinstance(obj, TenantScopedMixin):
            if obj.organization_id is None:
                if current_org is None:
                    raise TenantContextMissingError(
                        f"Cannot persist new {obj.__class__.__name__} without active tenant context."
                    )
                obj.organization_id = current_org
            elif current_org is not None and str(obj.organization_id) != str(current_org):
                raise TenantMismatchError(
                    f"New {obj.__class__.__name__} has organization_id={obj.organization_id}, "
                    f"which does not match active tenant={current_org}."
                )

    for obj in session.dirty:
        if isinstance(obj, TenantScopedMixin):
            state = inspect(obj)
            history = state.get_history("organization_id", True)
            if history.has_changes():
                if current_org is not None and str(obj.organization_id) != str(current_org):
                    raise TenantMismatchError(
                        f"Attempted to mutate organization_id on {obj.__class__.__name__} to {obj.organization_id}."
                    )

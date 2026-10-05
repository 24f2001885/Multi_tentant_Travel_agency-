"""
Unit tests for app/tenancy/scoping.py (TDR v2 §6, Milestone P7).
Enforces structural multi-tenant data isolation.
"""

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Trek, User
from app.tenancy.scoping import (
    TenantContextMissingError,
    TenantMismatchError,
    tenant_bypass,
    tenant_context,
)


@pytest.mark.postgres_only
def test_tenant_scoping_filter_on_select(app, org_a, org_b, make_trek):
    """select(Trek) returns only records belonging to the active tenant."""
    # print(f"[DEBUG] scoping test org_a={org_a.id} org_b={org_b.id}")
    trek_a = make_trek(org_a, name="Trek Alpha")
    trek_b = make_trek(org_b, name="Trek Beta")

    with app.app_context(), tenant_context(org_a.id):
        treks = db.session.execute(select(Trek)).scalars().all()
        ids = [t.id for t in treks]
        assert trek_a.id in ids
        assert trek_b.id not in ids

    with app.app_context(), tenant_context(org_b.id):
        treks = db.session.execute(select(Trek)).scalars().all()
        ids = [t.id for t in treks]
        assert trek_b.id in ids
        assert trek_a.id not in ids


@pytest.mark.postgres_only
def test_cross_tenant_query_returns_none(app, org_a, org_b, make_trek):
    """Querying a record belonging to another tenant yields None."""
    trek_b = make_trek(org_b, name="Trek of Org B")

    with app.app_context(), tenant_context(org_a.id):
        result = db.session.execute(
            select(Trek).where(Trek.id == trek_b.id)
        ).scalar_one_or_none()
        assert result is None


@pytest.mark.postgres_only
def test_auto_fill_organization_id_on_flush(app, org_a):
    """Instances created with organization_id=None are stamped with active org_id on flush."""
    from datetime import date, timedelta
    with app.app_context(), tenant_context(org_a.id):
        trek = Trek(
            name="Auto Fill Trek",
            location="Sikkim",
            difficulty="easy",
            duration_days=3,
            total_slots=12,
            available_slots=12,
            price=3000.0,
            status="open",
            start_date=date.today() + timedelta(days=5),
            end_date=date.today() + timedelta(days=8),
            # organization_id deliberately omitted/None
        )
        db.session.add(trek)
        db.session.flush()

        assert trek.organization_id == org_a.id
        db.session.commit()


@pytest.mark.postgres_only
def test_tenant_mismatch_raises_on_flush(app, org_a, org_b):
    """Attempting to persist an entity stamped for org_b inside tenant_context(org_a) raises TenantMismatchError."""
    from datetime import date, timedelta
    with app.app_context(), tenant_context(org_a.id):
        trek = Trek(
            organization_id=org_b.id,  # Mismatch!
            name="Conflicting Trek",
            location="Ladakh",
            difficulty="hard",
            duration_days=7,
            total_slots=8,
            available_slots=8,
            price=12000.0,
            status="open",
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=17),
        )
        db.session.add(trek)
        with pytest.raises(TenantMismatchError):
            db.session.flush()
        db.session.rollback()


@pytest.mark.postgres_only
def test_fail_closed_without_tenant_context(app, org_a):
    """Executing queries on tenant-scoped models outside of any tenant context or bypass raises TenantContextMissingError."""
    with app.app_context():
        # Neither tenant_context nor tenant_bypass is active
        with pytest.raises(TenantContextMissingError):
            db.session.execute(select(Trek)).all()


@pytest.mark.postgres_only
def test_tenant_bypass_allows_cross_tenant_access_and_nesting(app, org_a, org_b, make_trek):
    """tenant_bypass allows unrestricted cross-tenant queries and supports nested contexts."""
    trek_a = make_trek(org_a, name="Trek A")
    trek_b = make_trek(org_b, name="Trek B")

    with app.app_context(), tenant_bypass():
        # Outer bypass
        treks = db.session.execute(select(Trek)).scalars().all()
        ids = [t.id for t in treks]
        assert trek_a.id in ids
        assert trek_b.id in ids

        with tenant_bypass():
            # Nested bypass
            nested_treks = db.session.execute(select(Trek)).scalars().all()
            assert len(nested_treks) >= 2

        # Still bypassed after inner exit
        after_nested = db.session.execute(select(Trek)).scalars().all()
        assert len(after_nested) >= 2

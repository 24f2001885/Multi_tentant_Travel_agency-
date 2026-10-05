"""
Shared pytest fixtures and test configuration for the TMA Multi-Tenant Platform (TDR v2 §9, §14; Milestone P7).
"""

import os
import pytest
from datetime import datetime, timezone
from sqlalchemy import select, text
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import Organization, Domain, User, Trek, Booking, StaffProfile, PlatformAdmin
from app.tenancy.scoping import tenant_bypass, tenant_context


# Ensure HASHIDS_SALT is set for testing
os.environ.setdefault("HASHIDS_SALT", "test-hashids-salt-p7-secure-suite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-p7-tma")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "postgres_only: mark test to run only when backed by PostgreSQL"
    )


@pytest.fixture(autouse=True)
def skip_postgres_only_if_sqlite(request):
    """Skips tests marked with postgres_only if database is SQLite."""
    if request.node.get_closest_marker("postgres_only"):
        db_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
        if not db_url.startswith("postgresql"):
            pytest.skip("Test requires PostgreSQL (DATABASE_URL does not start with postgresql)")


@pytest.fixture(scope="session")
def app():
    """Session-scoped application fixture configured for testing."""
    test_db_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not test_db_url:
        test_db_url = "sqlite:///:memory:"

    if test_db_url.startswith("postgres://"):
        test_db_url = test_db_url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif test_db_url.startswith("postgresql://") and not test_db_url.startswith("postgresql+"):
        test_db_url = test_db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

    os.environ["DATABASE_URL"] = test_db_url
    os.environ["TEST_DATABASE_URL"] = test_db_url

    test_config = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": test_db_url,
        "WTF_CSRF_ENABLED": False,
    }

    application = create_app(test_config)
    # print(f"[DEBUG] conftest app db={application.config['SQLALCHEMY_DATABASE_URI'][:12]}")

    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def clean_db_session(app):
    """Isolates each test by rolling back any uncommitted database mutations."""
    with app.app_context():
        yield
        db.session.rollback()
        # Clean up database tables between tests to prevent row leaks
        with tenant_bypass():
            for table in reversed(db.metadata.sorted_tables):
                db.session.execute(table.delete())
            db.session.commit()
        db.session.remove()


from sqlalchemy.orm import make_transient
import uuid


@pytest.fixture
def platform_admin(app):
    """Fixture providing a Platform Admin."""
    with app.app_context(), tenant_bypass():
        db.session.expire_on_commit = False
        admin = PlatformAdmin(
            id=uuid.uuid4(),
            name="Platform Owner",
            email="owner@platform.local",
            is_active=True,
        )
        admin.set_password("change-me-platform-password")
        db.session.add(admin)
        db.session.commit()
        make_transient(admin)
        return admin


@pytest.fixture
def org_a(app, platform_admin):
    """Fixture providing Tenant A organization with domain a.localhost."""
    with app.app_context(), tenant_bypass():
        db.session.expire_on_commit = False
        org = Organization(
            name="Organization A",
            status="active",
            created_by_platform_admin_id=platform_admin.id,
        )
        db.session.add(org)
        db.session.flush()

        domain = Domain(
            organization_id=org.id,
            domain_name="a.localhost",
            domain_type="subdomain",
            is_primary=True,
        )
        db.session.add(domain)
        db.session.commit()

        _ = org.id
        _ = org.name
        _ = org.status
        make_transient(org)
        return org


@pytest.fixture
def org_b(app, platform_admin):
    """Fixture providing Tenant B organization with domain b.localhost."""
    with app.app_context(), tenant_bypass():
        db.session.expire_on_commit = False
        org = Organization(
            name="Organization B",
            status="active",
            created_by_platform_admin_id=platform_admin.id,
        )
        db.session.add(org)
        db.session.flush()

        domain = Domain(
            organization_id=org.id,
            domain_name="b.localhost",
            domain_type="subdomain",
            is_primary=True,
        )
        db.session.add(domain)
        db.session.commit()

        _ = org.id
        _ = org.name
        _ = org.status
        make_transient(org)
        return org


@pytest.fixture
def client_a(app, org_a):
    """Test client bound to Tenant A (Host: a.localhost)."""
    client = app.test_client()
    orig_open = client.open

    def bound_open(*args, **kwargs):
        headers = kwargs.get("headers")
        if headers is None:
            kwargs["headers"] = {"Host": "a.localhost"}
        elif isinstance(headers, dict) and "Host" not in headers:
            headers["Host"] = "a.localhost"
        return orig_open(*args, **kwargs)

    client.open = bound_open
    return client


@pytest.fixture
def client_b(app, org_b):
    """Test client bound to Tenant B (Host: b.localhost)."""
    client = app.test_client()
    orig_open = client.open

    def bound_open(*args, **kwargs):
        headers = kwargs.get("headers")
        if headers is None:
            kwargs["headers"] = {"Host": "b.localhost"}
        elif isinstance(headers, dict) and "Host" not in headers:
            headers["Host"] = "b.localhost"
        return orig_open(*args, **kwargs)

    client.open = bound_open
    return client


@pytest.fixture
def ctx_a(app, org_a):
    """Context manager fixture activating tenant_context for Organization A."""
    with app.app_context(), tenant_context(org_a.id):
        yield org_a


@pytest.fixture
def ctx_b(app, org_b):
    """Context manager fixture activating tenant_context for Organization B."""
    with app.app_context(), tenant_context(org_b.id):
        yield org_b


@pytest.fixture
def make_user(app):
    """Factory helper to create users stamped for a specific organization."""
    def _make(
        org,
        role="trekker",
        name="Test User",
        email=None,
        password="Password123!",
        mobile_country_code="+91",
        mobile_number="9876543210",
        terms_accepted=True,
        is_active=True,
    ):
        with tenant_context(org.id):
            db.session.expire_on_commit = False
            if not email:
                import uuid
                email = f"{role}-{uuid.uuid4().hex[:8]}@example.com"
            user = User(
                organization_id=org.id,
                name=name,
                email=email,
                password_hash=generate_password_hash(password),
                role=role,
                is_active=is_active,
                mobile_country_code=mobile_country_code,
                mobile_number=mobile_number,
                terms_accepted_at=datetime.now(timezone.utc) if terms_accepted else None,
            )
            db.session.add(user)
            db.session.commit()
            _ = user.id
            _ = user.email
            _ = user.role
            _ = user.organization_id
            make_transient(user)
            return user
    return _make


@pytest.fixture
def make_trek(app):
    """Factory helper to create treks stamped for a specific organization."""
    def _make(
        org,
        name="Valley of Flowers",
        location="Uttarakhand",
        difficulty="moderate",
        duration_days=5,
        total_slots=10,
        available_slots=10,
        price=7500.0,
        status="open",
        start_date=None,
        end_date=None,
    ):
        with tenant_context(org.id):
            from datetime import date, timedelta
            if start_date is None:
                start_date = date.today() + timedelta(days=10)
            if end_date is None:
                end_date = start_date + timedelta(days=duration_days)

            db.session.expire_on_commit = False
            trek = Trek(
                organization_id=org.id,
                name=name,
                location=location,
                difficulty=difficulty,
                duration_days=duration_days,
                total_slots=total_slots,
                available_slots=available_slots,
                price=price,
                status=status,
                start_date=start_date,
                end_date=end_date,
            )
            db.session.add(trek)
            db.session.commit()
            _ = trek.id
            _ = trek.name
            _ = trek.organization_id
            _ = trek.available_slots
            _ = trek.status
            make_transient(trek)
            return trek
    return _make

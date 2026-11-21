"""
Integration tests for Milestone P13 — Backup, Monitoring & Hardening.
TDR v2 §4.6; PDR v2 §5.14.
"""

from datetime import datetime, timezone
import json
import gzip
import os
import uuid
import pytest

from app.extensions import db
from app.models import Organization, Domain, User, Trek, Booking, PlatformAdmin, PlatformAccessLog
from app.models.tenant_backup import TenantBackup
from app.services import backup_service, monitoring_service
from app.tenancy.scoping import tenant_bypass, tenant_context
from werkzeug.security import generate_password_hash


@pytest.fixture
def p13_setup(app):
    with tenant_bypass():
        db.session.expire_on_commit = False
        # Platform Admin
        p_admin = PlatformAdmin(name="Super Boss", email="boss@platform.local", is_active=True)
        p_admin.set_password("pass")
        db.session.add(p_admin)
        db.session.flush()

        # Tenant 1
        org1 = Organization(
            name="Org One Backups",
            status="active",
            created_by_platform_admin_id=p_admin.id,
        )
        db.session.add(org1)
        db.session.flush()

        domain1 = Domain(
            organization_id=org1.id,
            domain_name="org1.localhost",
            domain_type="subdomain",
            is_primary=True,
        )
        db.session.add(domain1)

        admin1 = User(
            organization_id=org1.id,
            name="Admin One",
            email="admin1@backups.local",
            password_hash=generate_password_hash("pass"),
            role="admin",
            mobile_country_code="+91",
            mobile_number="9876543201",
            terms_accepted_at=datetime.now(timezone.utc),
        )
        db.session.add(admin1)
        db.session.flush()
        org1.tenant_admin_user_id = admin1.id

        # Trek and Booking for Org 1
        trek1 = Trek(
            organization_id=org1.id,
            name="Peak One Trek",
            location="Sikkim",
            difficulty="moderate",
            duration_days=5,
            total_slots=12,
            available_slots=10,
            price=8000,
            status="open",
            start_date=datetime.now(timezone.utc).date(),
            end_date=datetime.now(timezone.utc).date(),
        )
        db.session.add(trek1)
        db.session.flush()

        booking1 = Booking(
            organization_id=org1.id,
            user_id=admin1.id,
            trek_id=trek1.id,
            status="confirmed",
            slots=2,
            total_price=16000,
        )
        db.session.add(booking1)

        # Tenant 2
        org2 = Organization(
            name="Org Two Backups",
            status="active",
            created_by_platform_admin_id=p_admin.id,
        )
        db.session.add(org2)
        db.session.flush()

        domain2 = Domain(
            organization_id=org2.id,
            domain_name="org2.localhost",
            domain_type="subdomain",
            is_primary=True,
        )
        db.session.add(domain2)

        admin2 = User(
            organization_id=org2.id,
            name="Admin Two",
            email="admin2@backups.local",
            password_hash=generate_password_hash("pass"),
            role="admin",
            mobile_country_code="+91",
            mobile_number="9876543202",
            terms_accepted_at=datetime.now(timezone.utc),
        )
        db.session.add(admin2)
        db.session.flush()
        org2.tenant_admin_user_id = admin2.id

        db.session.commit()

        return {
            "org1": org1,
            "admin1": admin1,
            "org2": org2,
            "admin2": admin2,
            "p_admin": p_admin,
        }


def test_tenant_self_serve_backup_creation_and_contents(app, p13_setup):
    """
    Verifies that a tenant admin can generate their own backup,
    that it creates a valid .json.gz file containing their records,
    and that file size is recorded.
    """
    data = p13_setup
    org1 = data["org1"]
    admin1 = data["admin1"]

    backup = backup_service.create_tenant_backup(
        organization_id=org1.id,
        triggered_by_user_id=admin1.id,
    )
    assert backup.status == "completed"
    assert backup.organization_id == org1.id
    assert backup.file_size_bytes is not None
    assert backup.file_size_bytes > 0
    assert os.path.exists(backup.storage_location)

    # Decompress and verify JSON contents
    with gzip.open(backup.storage_location, "rb") as gz:
        content = json.loads(gz.read().decode("utf-8"))
    assert content["organization"]["name"] == "Org One Backups"
    assert len(content["treks"]) == 1
    assert content["treks"][0]["name"] == "Peak One Trek"
    assert len(content["bookings"]) == 1
    assert content["bookings"][0]["total_price"] == 16000.0


def test_tenant_backup_cross_tenant_isolation(client, app, p13_setup):
    """
    Verifies that Tenant Admin 2 cannot download Tenant Admin 1's backup archive.
    """
    data = p13_setup
    org1 = data["org1"]
    admin1 = data["admin1"]
    org2 = data["org2"]
    admin2 = data["admin2"]

    backup1 = backup_service.create_tenant_backup(
        organization_id=org1.id,
        triggered_by_user_id=admin1.id,
    )

    # 1. Admin 1 can download backup1
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin1.id)
        sess["capability_keys"] = []

    resp1 = client.get(
        f"/admin/backups/{backup1.id}/download",
        headers={"Host": "org1.localhost:5000"},
    )
    assert resp1.status_code == 200
    assert resp1.headers.get("Content-Type") in ("application/gzip", "application/x-gzip", "application/octet-stream")

    # 2. Admin 2 attempting to download backup1 on org2 host receives 404 (scoped isolation)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin2.id)
        sess["capability_keys"] = []

    resp2 = client.get(
        f"/admin/backups/{backup1.id}/download",
        headers={"Host": "org2.localhost:5000"},
    )
    assert resp2.status_code == 404


def test_platform_monitoring_and_full_backup(client, app, p13_setup):
    """
    Verifies Platform Admin monitoring metrics calculation and full platform backup triggering.
    """
    data = p13_setup
    p_admin = data["p_admin"]
    host = "platform.localhost:5000"

    # 1. Monitoring service returns aggregated data
    b_metrics = monitoring_service.get_platform_business_metrics()
    assert b_metrics["total_organizations"] >= 2
    assert b_metrics["gross_merchandise_value"] >= 16000.0

    i_metrics = monitoring_service.get_platform_infra_metrics()
    assert "users" in i_metrics["table_counts"]

    # 2. Login as platform admin and view monitoring dashboard
    with client.session_transaction() as sess:
        sess["platform_admin_id"] = str(p_admin.id)

    resp = client.get("/monitoring", headers={"Host": host})
    assert resp.status_code == 200
    assert b"Gross Merchandise Value" in resp.data

    # 3. Platform admin triggers full platform backup
    resp_trigger = client.post(
        "/backups/trigger",
        data={"target": "all"},
        headers={"Host": host},
        follow_redirects=True,
    )
    assert resp_trigger.status_code == 200
    assert b"Full platform backup created successfully" in resp_trigger.data

    # 4. Check that meta-audit log recorded the platform action
    with tenant_bypass():
        log = PlatformAccessLog.query.filter_by(action="full_platform_backup").first()
        assert log is not None
        assert log.platform_admin_id == p_admin.id

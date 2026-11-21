"""
Integration tests for Milestone P12 — Support v2 (Multi-turn Ticketing & Thread Model).
TDR v2 §4.4, §15; PDR v2 §5.12.
"""

from datetime import datetime, timezone
import uuid
import pytest
from flask import g
from sqlalchemy import select

from app.extensions import db
from app.models import Organization, Domain, User, Trek, Booking, Ticket, StaffProfile
from app.models.ticket_message import TicketMessage
from app.services import ticket_service
from app.services.id_codec import encode
from app.tenancy.scoping import tenant_context, tenant_bypass
from werkzeug.security import generate_password_hash


@pytest.fixture
def support_v2_setup(app, platform_admin):
    with tenant_bypass():
        org = Organization(
            name="P12 Support Org",
            status="active",
            created_by_platform_admin_id=platform_admin.id,
        )
        db.session.add(org)
        db.session.flush()

        domain = Domain(
            organization_id=org.id,
            domain_name="support-v2.localhost",
            domain_type="subdomain",
            is_primary=True,
        )
        db.session.add(domain)

        # Admin
        admin = User(
            organization_id=org.id,
            name="Org Admin",
            email="admin@support.local",
            password_hash=generate_password_hash("pass"),
            role="admin",
            mobile_country_code="+91",
            mobile_number="9876543210",
            terms_accepted_at=datetime.now(timezone.utc),
        )
        db.session.add(admin)
        db.session.flush()
        org.tenant_admin_user_id = admin.id

        # Trekker A (ticket owner)
        trekker_a = User(
            organization_id=org.id,
            name="Alice Trekker",
            email="alice@support.local",
            password_hash=generate_password_hash("pass"),
            role="trekker",
            mobile_country_code="+91",
            mobile_number="9876543211",
            terms_accepted_at=datetime.now(timezone.utc),
        )
        db.session.add(trekker_a)

        # Trekker B (outsider)
        trekker_b = User(
            organization_id=org.id,
            name="Bob Trekker",
            email="bob@support.local",
            password_hash=generate_password_hash("pass"),
            role="trekker",
            mobile_country_code="+91",
            mobile_number="9876543212",
            terms_accepted_at=datetime.now(timezone.utc),
        )
        db.session.add(trekker_b)

        # Staff user
        staff_user = User(
            organization_id=org.id,
            name="Guide Sam",
            email="sam@support.local",
            password_hash=generate_password_hash("pass"),
            role="staff",
            mobile_country_code="+91",
            mobile_number="9876543213",
            terms_accepted_at=datetime.now(timezone.utc),
        )
        db.session.add(staff_user)
        db.session.flush()

        staff_prof = StaffProfile(
            organization_id=org.id,
            user_id=staff_user.id,
            approval_status="approved",
            approved_by_admin_id=admin.id,
        )
        db.session.add(staff_prof)
        db.session.flush()

        # Trek assigned to Sam
        trek = Trek(
            organization_id=org.id,
            name="Alpine Pass",
            location="Himachal",
            difficulty="moderate",
            duration_days=4,
            total_slots=10,
            available_slots=8,
            price=5000,
            status="open",
            start_date=datetime.now(timezone.utc).date(),
            end_date=datetime.now(timezone.utc).date(),
            assigned_staff_id=staff_prof.id,
        )
        db.session.add(trek)
        db.session.flush()

        # Booking for Alice
        booking = Booking(
            organization_id=org.id,
            user_id=trekker_a.id,
            trek_id=trek.id,
            status="confirmed",
            slots=2,
            total_price=10000,
        )
        db.session.add(booking)
        db.session.commit()

        return {
            "org": org,
            "domain": domain,
            "admin": admin,
            "trekker_a": trekker_a,
            "trekker_b": trekker_b,
            "staff": staff_user,
            "trek": trek,
            "booking": booking,
        }


def test_multi_turn_ticketing_lifecycle(app, support_v2_setup):
    """
    Verifies multi-turn messaging:
    1. Trekker creates ticket
    2. Admin replies -> status answered, ticket remains open
    3. Trekker replies -> status open, thread grows
    4. Only raising trekker can mark resolved
    5. Once resolved, further replies are blocked
    """
    data = support_v2_setup
    org = data["org"]
    trekker_a = data["trekker_a"]
    admin = data["admin"]
    trekker_b = data["trekker_b"]

    with tenant_context(org.id):
        # 1. Raise ticket
        t = ticket_service.create_ticket(
            user_id=trekker_a.id,
            ticket_type="query",
            subject="Gear recommendation",
            message="What boots should I bring?",
        )
        assert t.status == "open"
        assert len(t.messages) == 0

        # 2. Admin posts reply
        msg1 = ticket_service.add_ticket_message(
            ticket=t,
            sender=admin,
            message_text="We recommend high-ankle waterproof boots.",
        )
        db.session.commit()

        refreshed = db.session.get(Ticket, t.id)
        assert refreshed.status == "answered"
        assert len(refreshed.messages) == 1
        assert refreshed.messages[0].message == "We recommend high-ankle waterproof boots."

        # 3. Trekker A posts follow-up message
        msg2 = ticket_service.add_ticket_message(
            ticket=refreshed,
            sender=trekker_a,
            message_text="Do you have recommendations for crampons?",
        )
        db.session.commit()

        refreshed = db.session.get(Ticket, t.id)
        assert refreshed.status == "open"
        assert len(refreshed.messages) == 2

        # 4. Non-raising user (Bob or Admin) cannot mark resolved
        with pytest.raises(ticket_service.UnauthorizedTicketActionError):
            ticket_service.resolve_ticket(refreshed, resolver=trekker_b)

        # 5. Raising trekker (Alice) marks resolved
        ticket_service.resolve_ticket(refreshed, resolver=trekker_a)
        db.session.commit()

        refreshed = db.session.get(Ticket, t.id)
        assert refreshed.status == "resolved"

        # 6. Replying to resolved ticket raises TicketResolvedError
        with pytest.raises(ticket_service.TicketResolvedError):
            ticket_service.add_ticket_message(
                ticket=refreshed,
                sender=admin,
                message_text="Another reply after resolution.",
            )


def test_http_support_v2_thread_flow(client, app, support_v2_setup):
    """
    Tests HTTP endpoints for ticket detail, reply posting, and resolving.
    """
    data = support_v2_setup
    org = data["org"]
    trekker_a = data["trekker_a"]
    admin = data["admin"]
    host = "support-v2.localhost:5000"

    with tenant_context(org.id):
        ticket = ticket_service.create_ticket(
            user_id=trekker_a.id,
            ticket_type="query",
            subject="Weather inquiry",
            message="Is rain expected this weekend?",
        )
        ticket_hid = encode(ticket.id)

    # Login as Admin and view ticket
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin.id)
        sess["capability_keys"] = ["tickets.view", "tickets.respond"]

    resp = client.get(f"/tickets/{ticket_hid}", headers={"Host": host})
    assert resp.status_code == 200
    assert b"Weather inquiry" in resp.data

    # Admin posts reply via HTTP
    resp = client.post(
        f"/tickets/{ticket_hid}/reply",
        data={"message": "Forecast indicates clear skies on Saturday."},
        headers={"Host": host},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Forecast indicates clear skies on Saturday." in resp.data

    # Now login as Trekker A and view ticket
    with client.session_transaction() as sess:
        sess["_user_id"] = str(trekker_a.id)
        sess["capability_keys"] = []

    resp = client.get(f"/tickets/{ticket_hid}", headers={"Host": host})
    assert resp.status_code == 200
    assert b"Forecast indicates clear skies on Saturday." in resp.data
    # Should see "Mark Resolved" button
    assert b"Mark Resolved" in resp.data

    # Trekker posts reply
    resp = client.post(
        f"/tickets/{ticket_hid}/reply",
        data={"message": "Great, thanks for confirming!"},
        headers={"Host": host},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Great, thanks for confirming!" in resp.data

    # Trekker marks resolved
    resp = client.post(
        f"/tickets/{ticket_hid}/resolve",
        headers={"Host": host},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"resolved" in resp.data


def test_ticket_ui_wireframe_and_badge_uniformity(client, app, support_v2_setup):
    """
    Verifies that ticket views render the correct wireframe layout:
    - Admin/Staff: Left-hand full-height sidebar (flex-md-row), no public consumer footer.
    - Trekker: Standard trekker navbar (flex-column), consumer footer present.
    - Status badge correctly displays the ticket status ('open', 'answered', 'resolved'), NOT 'TICKET_STATUS'.
    """
    data = support_v2_setup
    org = data["org"]
    trekker_a = data["trekker_a"]
    admin = data["admin"]
    staff_user = data["staff"]
    booking = data["booking"]
    host = "support-v2.localhost:5000"

    with tenant_context(org.id):
        ticket = ticket_service.create_ticket(
            user_id=trekker_a.id,
            ticket_type="problem",
            subject="Gear issue",
            message="Broken trekking pole.",
            booking_id=booking.id,
        )
        ticket_hid = encode(ticket.id)

    # 1. Admin accessing ticket detail
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin.id)
        sess["capability_keys"] = ["tickets.view", "tickets.respond"]

    resp_admin = client.get(f"/tickets/{ticket_hid}", headers={"Host": host})
    assert resp_admin.status_code == 200
    html_admin = resp_admin.data.decode("utf-8")
    assert "adminSidebar" in html_admin
    assert "flex-column flex-md-row" in html_admin
    assert "<footer class=" not in html_admin
    # Ensure badge does NOT render the macro domain name "TICKET_STATUS"
    assert "TICKET_STATUS" not in html_admin
    assert ">open</span>" in html_admin

    # Admin accessing ticket list
    resp_list = client.get("/tickets", headers={"Host": host})
    assert resp_list.status_code == 200
    html_list = resp_list.data.decode("utf-8")
    assert "adminSidebar" in html_list
    assert "flex-column flex-md-row" in html_list
    assert "<footer class=" not in html_list
    assert "TICKET_STATUS" not in html_list
    assert ">open</span>" in html_list

    # 2. Staff accessing ticket detail
    with client.session_transaction() as sess:
        sess["_user_id"] = str(staff_user.id)
        sess["capability_keys"] = ["tickets.view", "tickets.respond"]

    resp_staff = client.get(f"/tickets/{ticket_hid}", headers={"Host": host})
    assert resp_staff.status_code == 200
    html_staff = resp_staff.data.decode("utf-8")
    assert "staffSidebar" in html_staff
    assert "flex-column flex-md-row" in html_staff
    assert "<footer class=" not in html_staff
    assert "TICKET_STATUS" not in html_staff
    assert ">open</span>" in html_staff

    # 3. Trekker accessing ticket detail
    with client.session_transaction() as sess:
        sess["_user_id"] = str(trekker_a.id)
        sess["capability_keys"] = []

    resp_trekker = client.get(f"/tickets/{ticket_hid}", headers={"Host": host})
    assert resp_trekker.status_code == 200
    html_trekker = resp_trekker.data.decode("utf-8")
    assert "adminSidebar" not in html_trekker
    assert "staffSidebar" not in html_trekker
    assert "trekkerNav" in html_trekker
    assert "<footer class=" in html_trekker
    assert "TICKET_STATUS" not in html_trekker
    assert ">open</span>" in html_trekker


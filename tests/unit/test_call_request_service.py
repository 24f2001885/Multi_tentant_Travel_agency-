"""
Unit tests for app/services/call_request_service.py (TDR v2 §4.5, Milestone P7).
Tests lifecycle, mobile phone validation, and state machine transitions.
"""

import pytest
from app.extensions import db
from app.models.call_request import CallRequest
from app.services import call_request_service
from app.services.auth_service import InvalidMobileError
from app.services.call_request_service import InvalidCallRequestTransition
from app.tenancy.scoping import tenant_context


def test_valid_call_request_creation(app, org_a):
    """Successfully creates a CallRequest with status 'new' and normalized E.164 phone."""
    with app.app_context(), tenant_context(org_a.id):
        req = call_request_service.create_call_request(
            name="John Doe",
            phone="+919876543210",
            preferred_time="Morning 10 AM",
            message="Looking for family trek details",
        )
        db.session.commit()
        # print(f"[DEBUG] call_request test id={req.id}")

        assert req.id is not None
        assert req.name == "John Doe"
        assert req.phone == "+919876543210"
        assert req.status == "new"
        assert req.organization_id == org_a.id
        assert req.handled_by is None


def test_invalid_phone_rejected_no_row_created(app, org_a):
    """Invalid phone number is rejected via InvalidMobileError and writes nothing to the database."""
    with app.app_context(), tenant_context(org_a.id):
        initial_count = CallRequest.query.count()

        with pytest.raises(InvalidMobileError):
            call_request_service.create_call_request(
                name="Jane Doe",
                phone="not-a-phone-number",
            )

        db.session.rollback()
        assert CallRequest.query.count() == initial_count


def test_legal_status_transitions_and_handled_by(app, org_a, make_user):
    """Legal transitions update status; handler is recorded on first move out of 'new'."""
    admin_user = make_user(org_a, role="admin", name="Admin Handler")

    with app.app_context(), tenant_context(org_a.id):
        req = call_request_service.create_call_request(
            name="Alice Smith",
            phone="+919876543211",
        )
        db.session.commit()
        assert req.status == "new"
        assert req.handled_by is None

        # Transition: new -> contacted
        call_request_service.update_status(req, "contacted", handler_user=admin_user)
        db.session.commit()

        assert req.status == "contacted"
        assert req.handled_by == admin_user.id

        # Transition: contacted -> closed
        call_request_service.update_status(req, "closed")
        db.session.commit()

        assert req.status == "closed"
        assert req.handled_by == admin_user.id


def test_reopen_closed_call_request(app, org_a):
    """Closed call requests can be reopened back to 'new'."""
    with app.app_context(), tenant_context(org_a.id):
        req = call_request_service.create_call_request(
            name="Bob Brown",
            phone="+919876543212",
        )
        call_request_service.update_status(req, "closed")
        db.session.commit()
        assert req.status == "closed"

        # Reopen closed call request back to new
        call_request_service.update_status(req, "new")
        db.session.commit()
        assert req.status == "new"


def test_illegal_status_transition_raises_error(app, org_a):
    """Illegal transitions (e.g. contacted -> new or invalid status) raise InvalidCallRequestTransition."""
    with app.app_context(), tenant_context(org_a.id):
        req = call_request_service.create_call_request(
            name="Bob Brown",
            phone="+919876543212",
        )
        call_request_service.update_status(req, "contacted")
        db.session.commit()
        assert req.status == "contacted"

        # Attempt illegal transition from contacted back to new
        with pytest.raises(InvalidCallRequestTransition, match="Cannot transition CallRequest"):
            call_request_service.update_status(req, "new")

        # Attempt illegal transition with unsupported status
        with pytest.raises(InvalidCallRequestTransition, match="Cannot transition CallRequest"):
            call_request_service.update_status(req, "cancelled")

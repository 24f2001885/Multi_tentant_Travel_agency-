
from flask import g, request
from flask_login import current_user, login_required
from flask_restful import Resource
from marshmallow import Schema, fields

from app.auth.decorators import ticket_access_required
from app.extensions import db
from app.services import notification_service, ticket_service
from app.services.ticket_service import (
    AlreadyRespondedError,
    BookingOwnershipError,
    MissingBookingReferenceError,
)


def _error(code, message, status_code):
    return {"error": {"code": code, "message": message}}, status_code


class TicketSchema(Schema):
    id = fields.Int()
    user_id = fields.Int()
    trek_id = fields.Int(allow_none=True)
    booking_id = fields.Int(allow_none=True)
    type = fields.Str()
    subject = fields.Str()
    message = fields.Str()
    status = fields.Str()
    response = fields.Str(allow_none=True)
    responded_by_id = fields.Int(allow_none=True)
    responded_at = fields.DateTime(allow_none=True)
    created_at = fields.DateTime()


class TicketListResource(Resource):
    method_decorators = [login_required]

    def get(self):
        status = request.args.get("status")
        type_ = request.args.get("type")
        tickets = ticket_service.get_scoped_tickets(current_user, status=status, type_=type_)
        return {"items": TicketSchema(many=True).dump(tickets)}, 200

    def post(self):
        if current_user.role != "trekker":
            return _error("FORBIDDEN", "Only trekkers may raise tickets.", 403)

        payload = request.get_json(silent=True) or {}
        ticket_type = payload.get("type")
        subject = payload.get("subject")
        message = payload.get("message")
        if not ticket_type or not subject or not message:
            return _error("VALIDATION_ERROR", "type, subject, and message are required.", 422)

        try:
            ticket = ticket_service.create_ticket(
                user_id=current_user.id,
                ticket_type=ticket_type,
                subject=subject,
                message=message,
                booking_id=payload.get("booking_id"),
            )
        except MissingBookingReferenceError:
            return _error("MISSING_BOOKING_REFERENCE", "A Problem ticket requires a booking_id.", 422)
        except BookingOwnershipError:
            return _error("BOOKING_OWNERSHIP_ERROR", "That booking does not belong to you.", 422)

        return TicketSchema().dump(ticket), 201


class TicketResource(Resource):
    method_decorators = [ticket_access_required]

    def get(self, id):
        return TicketSchema().dump(g.ticket), 200


class TicketRespondResource(Resource):
    method_decorators = [ticket_access_required]

    def put(self, id):
        if current_user.role not in ("staff", "admin"):
            return _error("FORBIDDEN", "Only staff or admin may respond to tickets.", 403)

        payload = request.get_json(silent=True) or {}
        response_text = payload.get("response")
        if not response_text:
            return _error("VALIDATION_ERROR", "response is required.", 422)

        try:
            ticket_service.respond_ticket(
                g.ticket, responder=current_user, response_text=response_text
            )
        except AlreadyRespondedError:
            return _error("ALREADY_RESPONDED", "This ticket already has a response.", 409)

        db.session.commit()
        notification_service.send_ticket_response_email(g.ticket)
        return TicketSchema().dump(g.ticket), 200


class TicketResolveResource(Resource):
    method_decorators = [ticket_access_required]

    def put(self, id):
        if current_user.id != g.ticket.user_id:
            return _error("FORBIDDEN", "Only the raising trekker may resolve this ticket.", 403)

        ticket_service.resolve_ticket(g.ticket)
        db.session.commit()
        return TicketSchema().dump(g.ticket), 200
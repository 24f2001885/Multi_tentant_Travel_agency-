
from flask import request
from flask_login import current_user, login_required
from flask_restful import Resource
from marshmallow import Schema, fields

from app.extensions import db
from app.services import history_service, payment_service
from app.services.booking_service import (
    DuplicateActiveBookingError,
    TrekUnavailableError,
    initiate_booking,
)


def _error(code, message, status_code):
    return {"error": {"code": code, "message": message}}, status_code


class BookingSchema(Schema):
    id = fields.Int()
    user_id = fields.Int()
    trek_id = fields.Int()
    booking_date = fields.DateTime()
    status = fields.Str()
    payment_status = fields.Str()


def _scoped_booking_or_none(booking_id):
    scoped = history_service.get_booking_history(current_user)
    return next((b for b in scoped if b.id == booking_id), None)


class BookingListResource(Resource):
    method_decorators = [login_required]

    def get(self):
        status = request.args.get("status")
        bookings = history_service.get_booking_history(current_user, status=status)
        return {"items": BookingSchema(many=True).dump(bookings)}, 200

    def post(self):
        if current_user.role != "trekker":
            return _error("FORBIDDEN", "Only trekkers may create bookings.", 403)

        payload = request.get_json(silent=True) or {}
        trek_id = payload.get("trek_id")
        if not trek_id:
            return _error("VALIDATION_ERROR", "trek_id is required.", 422)

        try:
            booking = initiate_booking(current_user.id, trek_id)
        except TrekUnavailableError:
            return _error("SLOT_UNAVAILABLE", "This trek has no available slots.", 409)
        except DuplicateActiveBookingError:
            return _error(
                "DUPLICATE_BOOKING", "You already have an active booking for this trek.", 409
            )

        order = payment_service.create_gateway_order(booking)
        return {
            "booking": BookingSchema().dump(booking),
            "payment_order": {
                "gateway_order_id": order["id"],
                "amount": order["amount"],
                "currency": order["currency"],
            },
        }, 201


class BookingResource(Resource):
    method_decorators = [login_required]

    def get(self, id):
        booking = _scoped_booking_or_none(id)
        if booking is None:
            return _error("NOT_FOUND", "Booking not found.", 404)
        return BookingSchema().dump(booking), 200

    def put(self, id):
        booking = _scoped_booking_or_none(id)
        if booking is None:
            return _error("NOT_FOUND", "Booking not found.", 404)

        payload = request.get_json(silent=True) or {}
        if payload.get("action") != "cancel":
            return _error("VALIDATION_ERROR", "Only the 'cancel' action is supported.", 422)

        if booking.user_id != current_user.id:
            return _error("FORBIDDEN", "Only the booking owner may cancel it.", 403)

        if booking.status not in ("pending", "booked"):
            return _error("INVALID_STATE", "Only a pending or booked booking may be cancelled.", 409)

        if booking.status == "booked":
            payment_service.cancel_booking(booking, actor=current_user)
        else:
            booking.status = "cancelled"

        db.session.commit()
        return BookingSchema().dump(booking), 200
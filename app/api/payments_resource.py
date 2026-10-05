"""Payment API endpoints: the Razorpay webhook handler and a read-only
endpoint for looking up the payment attempts on a booking."""

from flask import request
from flask_login import current_user, login_required
from flask_restful import Resource

from app.extensions import csrf
from app.models import Booking, Payment
from app.services import payment_service
from app.services.payment_service import InvalidWebhookSignatureError

EVENT_STATUS_MAP = {
    "payment.captured": "success",
    "payment.failed": "failed",
}


class PaymentWebhookResource(Resource):
    # Razorpay calls this directly (no session/cookie), so it has to be
    # exempt from CSRF like any other server-to-server webhook.
    method_decorators = [csrf.exempt]

    def post(self):
        raw_body = request.get_data()
        signature = request.headers.get("X-Razorpay-Signature", "")

        try:
            payment_service.verify_webhook(raw_body, signature)
        except InvalidWebhookSignatureError:
            return "", 400

        payload = request.get_json(silent=True) or {}
        gateway_status = EVENT_STATUS_MAP.get(payload.get("event"))
        entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = entity.get("order_id")
        transaction_id = entity.get("id")

        if gateway_status and order_id:
            payment_service.confirm_payment(order_id, gateway_status, transaction_id)

        return {"received": True}, 200


class PaymentListForBookingResource(Resource):
    method_decorators = [login_required]

    def get(self, booking_id):
        booking = Booking.query.get(booking_id)
        if booking is None:
            return {"error": {"code": "NOT_FOUND", "message": "Booking not found."}}, 404

        # admin sees everything; staff only bookings on treks assigned to
        # them; trekkers only their own bookings
        is_owner = current_user.role == "trekker" and booking.user_id == current_user.id
        is_assigned_staff = (
            current_user.role == "staff"
            and current_user.staff_profile is not None
            and booking.trek.assigned_staff_id == current_user.staff_profile.id
        )
        is_admin = current_user.role == "admin"

        if not (is_owner or is_assigned_staff or is_admin):
            return {"error": {"code": "FORBIDDEN", "message": "You cannot view these payments."}}, 403

        # a failed attempt gets its own row rather than overwriting the
        # previous one, so a booking can have more than one payment here
        payments = Payment.query.filter_by(booking_id=booking_id).all()
        items = [
            {
                "id": p.id,
                "booking_id": p.booking_id,
                "amount": str(p.amount),
                "currency": p.currency,
                "gateway_order_id": p.gateway_order_id,
                "transaction_id": p.transaction_id,
                "status": p.status,
                "payment_date": p.payment_date.isoformat() if p.payment_date else None,
            }
            for p in payments
        ]
        return {"items": items}, 200

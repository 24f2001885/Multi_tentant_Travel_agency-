from datetime import datetime, timedelta

import razorpay
from flask import current_app
from sqlalchemy import text

from app.extensions import db
from app.models import Booking, Payment, Trek
from app.services import notification_service


class InvalidWebhookSignatureError(Exception):
    
    pass


def _client() -> razorpay.Client:
    return razorpay.Client(
        auth=(current_app.config["RAZORPAY_KEY_ID"], current_app.config["RAZORPAY_KEY_SECRET"])
    )


def create_gateway_order(booking: Booking) -> dict:
    
    trek = booking.trek
    order = _client().order.create({
        "amount": int(trek.price * 100),
        "currency": "INR",
        "receipt": str(booking.id),
    })

    payment = Payment(
        booking_id=booking.id,
        amount=trek.price,
        currency="INR",
        gateway_order_id=order["id"],
        status="initiated",
    )
    db.session.add(payment)
    db.session.commit()
    return order


def verify_webhook(payload, signature: str) -> None:
 
    secret = current_app.config.get("RAZORPAY_WEBHOOK_SECRET") or ""
    if not signature or not secret:
        raise InvalidWebhookSignatureError("Missing signature or RAZORPAY_WEBHOOK_SECRET")

    # Decode bytes payload to UTF-8 string to prevent Razorpay SDK TypeError
    payload_str = payload.decode("utf-8") if isinstance(payload, bytes) else payload

    try:
        _client().utility.verify_webhook_signature(payload_str, signature, secret)
    except Exception as exc:
        raise InvalidWebhookSignatureError(str(exc)) from exc


def confirm_payment(gateway_order_id: str, gateway_status: str, transaction_id: str | None) -> Payment:
    
    payment = Payment.query.filter_by(gateway_order_id=gateway_order_id).first()
    if payment is None:
        raise ValueError(f"No payment found for gateway_order_id={gateway_order_id}")

    # Idempotency: Razorpay retries webhooks; only 'initiated' payments are processed.
    if payment.status != "initiated":
        return payment

    db.session.commit()
    db.session.execute(text("BEGIN IMMEDIATE"))

    booking = Booking.query.get(payment.booking_id)
    trek = Trek.query.get(booking.trek_id)

    if gateway_status == "success":
        if trek.status == "open" and trek.available_slots >= 1:
            # Winner path
            trek.available_slots -= 1
            booking.status = "booked"
            booking.payment_status = "paid"
            payment.status = "success"
            payment.transaction_id = transaction_id
            payment.payment_date = datetime.utcnow()
            db.session.commit()
            try:
                notification_service.send_booking_confirmed_email(booking)
            except Exception:
                current_app.logger.exception(
                    "send_booking_confirmed_email failed for booking_id=%s", booking.id
                )
        else:
            # Loser path: slot vanished between initiation and confirmation.
            booking.status = "cancelled"
            booking.payment_status = "refunded"
            payment.status = "refunded"
            db.session.commit()
    elif gateway_status == "failed":
        booking.status = "cancelled"
        booking.payment_status = "failed"
        payment.status = "failed"
        db.session.commit()
    else:
        db.session.rollback()
        raise ValueError(f"Unrecognized gateway_status={gateway_status}")

    return payment


def cancel_booking(booking: Booking, actor) -> Booking:
    
    if booking.status != "booked":
        raise ValueError("Only a 'booked' booking can be cancelled via cancel_booking().")
    trek = Trek.query.get(booking.trek_id)
    trek.available_slots += 1
    booking.status = "cancelled"
    return booking


def expire_stale_bookings() -> int:
    
    cutoff = datetime.utcnow() - timedelta(minutes=30)
    stale = Booking.query.filter(Booking.status == "pending", Booking.booking_date < cutoff).all()

    count = 0
    for booking in stale:
        booking.status = "cancelled"
        for payment in Payment.query.filter_by(booking_id=booking.id, status="initiated").all():
            payment.status = "failed"
        count += 1

    if count:
        db.session.commit()
    return count
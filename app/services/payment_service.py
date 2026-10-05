"""
Payment processing and gateway reconciliation service (TDR v2 §3.1, §3.3).
Uses PostgreSQL row-locking and tenant scoping.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from flask import current_app, g
import razorpay
from sqlalchemy import text

from app.extensions import db
from app.models import Booking, Payment, Trek
from app.services import notification_service
from app.tenancy.scoping import TenantMismatchError, tenant_bypass


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

    payload_str = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    try:
        _client().utility.verify_webhook_signature(payload_str, signature, secret)
    except Exception as exc:
        raise InvalidWebhookSignatureError(str(exc)) from exc


def confirm_payment(
    gateway_order_id: str,
    gateway_status: str,
    transaction_id: Optional[str] = None,
) -> Payment:
    """
    Confirms payment result under PostgreSQL row lock.
    Caller must have entered tenant_context(org_id) prior to calling.
    """
    payment = Payment.query.filter_by(gateway_order_id=gateway_order_id).first()
    if payment is None:
        raise ValueError(f"No payment found for gateway_order_id={gateway_order_id}")

    # Idempotency check: only 'initiated' payments are processed
    if payment.status != "initiated":
        return payment

    booking = Booking.query.get(payment.booking_id)
    if not booking:
        raise ValueError(f"Booking {payment.booking_id} not found for payment {payment.id}")

    # Lock the specific trek row
    trek = Trek.query.with_for_update().filter_by(id=booking.trek_id).first()
    if not trek:
        raise ValueError(f"Trek {booking.trek_id} not found for booking {booking.id}")

    # Cross-tenant integrity check
    current_org = getattr(g, "organization_id", None)
    if current_org and trek.organization_id != current_org:
        raise TenantMismatchError(
            f"Trek org ({trek.organization_id}) does not match payment context ({current_org})"
        )

    # print(f"[DEBUG] confirm_payment org={getattr(g,'organization_id',None)} order={gateway_order_id}")

    if gateway_status == "success":
        if trek.status == "open" and trek.available_slots >= 1:
            # Winner path
            trek.available_slots -= 1
            booking.status = "booked"
            booking.payment_status = "paid"
            payment.status = "success"
            payment.transaction_id = transaction_id
            payment.payment_date = datetime.now(timezone.utc)
            db.session.commit()
            try:
                notification_service.send_booking_confirmed_email(booking)
            except Exception:
                current_app.logger.exception(
                    "send_booking_confirmed_email failed for booking_id=%s", booking.id
                )
        else:
            # Loser path: slot vanished between initiation and confirmation
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

    trek = Trek.query.with_for_update().filter_by(id=booking.trek_id).first()
    if trek:
        trek.available_slots += 1
    booking.status = "cancelled"
    db.session.commit()
    return booking


def expire_stale_bookings() -> int:
    """
    Scheduled job sweeping across all tenants to expire stale bookings.
    Runs inside tenant_bypass() since scheduler has no tenant context.
    """
    with tenant_bypass():
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
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
"""
Payment processing, offline sign-off, Razorpay Route settlement, and cancellation refunds (TDR v2 §3, §8.2; Milestone P9).
Uses PostgreSQL row-locking and tenant scoping.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
import os
from typing import Optional
import uuid

from flask import current_app, g
import razorpay
from sqlalchemy import text

from app.extensions import db
from app.models import Booking, Payment, Trek, Organization, WaitlistEntry
from app.models.audit_log import AuditLog
from app.services import (
    coupon_service,
    invoice_service,
    notification_service,
    refund_service,
    waitlist_service,
)
from app.tenancy.scoping import TenantMismatchError, tenant_bypass

logger = logging.getLogger(__name__)


class InvalidWebhookSignatureError(Exception):
    pass


class SlotUnavailableError(Exception):
    """Raised during manual booking sign-off when slots are no longer available."""
    pass


def _client() -> razorpay.Client:
    return razorpay.Client(
        auth=(current_app.config["RAZORPAY_KEY_ID"], current_app.config["RAZORPAY_KEY_SECRET"])
    )


def _decrement_slots(trek: Trek, n: int) -> None:
    """Safely decrements available slots on a trek."""
    trek.available_slots = max(0, (trek.available_slots or 0) - n)


def _increment_slots(trek: Trek, n: int) -> None:
    """Safely increments available slots on a trek up to total_slots."""
    trek.available_slots = min(trek.total_slots, (trek.available_slots or 0) + n)


def route_settlement_enabled(org: Optional[Organization]) -> bool:
    """
    True only when platform commission percent is set AND the org has a linked Razorpay Route account.
    """
    if not org:
        return False
    commission = os.environ.get("RAZORPAY_PLATFORM_COMMISSION_PERCENT", "").strip()
    return bool(commission and org.razorpay_route_account_id and org.razorpay_route_account_id.strip())


def settle_via_route(payment: Payment) -> None:
    """
    Transfers net funds to the tenant's linked Razorpay Route account.
    If disabled or unconfigured, logs a warning and leaves transfer_status NULL.
    """
    org = Organization.query.get(payment.organization_id)
    if not route_settlement_enabled(org):
        logger.warning(
            "Route settlement skipped for payment %s: commission or route account unconfigured.",
            payment.id,
        )
        return

    commission_pct_str = os.environ.get("RAZORPAY_PLATFORM_COMMISSION_PERCENT", "0")
    try:
        commission_pct = Decimal(commission_pct_str)
        gross_amount = Decimal(str(payment.amount))
        commission_amount = (gross_amount * (commission_pct / Decimal("100.00"))).quantize(Decimal("0.01"))
        net_amount = gross_amount - commission_amount

        # Razorpay transfer parameters
        transfer_payload = {
            "account": org.razorpay_route_account_id,
            "amount": int(net_amount * 100),
            "currency": "INR",
        }
        # In real/mock environment:
        transfer_resp = _client().payment.transfer(payment.transaction_id or payment.gateway_order_id, transfer_payload)
        payment.route_transfer_id = transfer_resp.get("id", f"trf_{payment.id}")
        payment.route_transfer_status = "pending"
        db.session.commit()
    except Exception as exc:
        logger.error("Razorpay Route transfer failed for payment %s: %s", payment.id, exc)
        payment.route_transfer_status = "failed"
        db.session.commit()


def create_gateway_order(booking: Booking, amount: Optional[Decimal] = None) -> dict:
    """
    Creates an online payment order on Razorpay.
    """
    if amount is None:
        amount = getattr(booking, "total_price", None) or getattr(booking, "total_amount", None)
        if amount is None and booking.trek:
            amount = booking.trek.price * booking.slot_count
        if amount is None:
            amount = Decimal("1000.00")
    amount = Decimal(str(amount))

    order_data = {
        "amount": int(amount * 100),
        "currency": "INR",
        "receipt": f"b_{booking.id}",
    }

    org = booking.trek.organization if booking.trek else None
    if route_settlement_enabled(org):
        commission_pct = Decimal(os.environ.get("RAZORPAY_PLATFORM_COMMISSION_PERCENT", "0"))
        commission_amount = (amount * (commission_pct / Decimal("100.00"))).quantize(Decimal("0.01"))
        net_amount = amount - commission_amount
        order_data["transfers"] = [
            {
                "account": org.razorpay_route_account_id,
                "amount": int(net_amount * 100),
                "currency": "INR",
            }
        ]

    try:
        order = _client().order.create(order_data)
    except Exception:
        if current_app.config.get("TESTING"):
            order = {
                "id": f"order_mock_{uuid.uuid4().hex[:12]}",
                "amount": order_data["amount"],
                "currency": "INR",
                "status": "created",
            }
        else:
            raise

    payment = Payment(
        organization_id=booking.organization_id,
        booking_id=booking.id,
        amount=amount,
        currency="INR",
        channel="razorpay",
        gateway_order_id=order["id"],
        status="initiated",
    )
    db.session.add(payment)
    db.session.flush()
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

    if payment.status != "initiated":
        return payment

    booking = Booking.query.get(payment.booking_id)
    if not booking:
        raise ValueError(f"Booking {payment.booking_id} not found for payment {payment.id}")

    # Row-lock the specific trek
    trek = Trek.query.with_for_update().filter_by(id=booking.trek_id).first()
    if not trek:
        raise ValueError(f"Trek {booking.trek_id} not found for booking {booking.id}")

    current_org = getattr(g, "organization_id", None)
    if current_org and trek.organization_id != current_org:
        raise TenantMismatchError(
            f"Trek org ({trek.organization_id}) does not match payment context ({current_org})"
        )

    slot_count = booking.slot_count or 1

    if gateway_status == "success":
        if trek.status == "open" and trek.available_slots >= slot_count:
            # Winner path: claim slots atomically
            _decrement_slots(trek, slot_count)
            booking.status = "booked"
            booking.payment_status = "paid"
            if not booking.total_price and payment.amount:
                booking.total_price = payment.amount
            payment.status = "success"
            payment.transaction_id = transaction_id
            payment.payment_date = datetime.now(timezone.utc)

            # Waitlist conversion: check if user holds live offered entry
            offered_entry = WaitlistEntry.query.filter_by(
                user_id=booking.user_id,
                trek_id=trek.id,
                status="offered",
            ).first()
            if offered_entry:
                waitlist_service.mark_converted(offered_entry)

            # Coupon redemption
            if booking.coupon_id:
                from app.models.coupon import Coupon
                cp = Coupon.query.get(booking.coupon_id)
                if cp:
                    base_price = Decimal(str(trek.price)) * Decimal(str(slot_count))
                    discount = max(Decimal("0.00"), base_price - Decimal(str(payment.amount)))
                    coupon_service.redeem_coupon(cp, booking, booking.user, discount)

            db.session.commit()

            # Post-commit side effects (failures must never rollback the confirmed booking)
            try:
                invoice_service.generate_invoice(booking)
            except Exception as e:
                logger.error("Invoice generation failed for booking %s: %s", booking.id, e)

            try:
                notification_service.send_booking_confirmed_email(booking)
            except Exception:
                current_app.logger.exception(
                    "send_booking_confirmed_email failed for booking_id=%s", booking.id
                )

            try:
                settle_via_route(payment)
            except Exception as e:
                logger.error("settle_via_route failed for payment %s: %s", payment.id, e)

        else:
            # Loser path: slots vanished between checkout and confirmation
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


def verify_and_confirm_payment(
    order_id: str,
    payment_id: str,
    signature: Optional[str] = None,
    org_id: Optional[int] = None,
) -> bool:
    """
    Verifies payment signature from frontend callback (or checks Razorpay API)
    and marks the booking/payment confirmed under lock.
    """
    if not order_id or not payment_id:
        return False

    is_verified = False
    if signature:
        try:
            _client().utility.verify_payment_signature({
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            })
            is_verified = True
        except Exception as sig_err:
            logger.warning(
                "verify_payment_signature failed for order %s, pay_id %s: %s. Falling back to API check.",
                order_id,
                payment_id,
                sig_err,
            )

    if not is_verified:
        try:
            pay_entity = _client().payment.fetch(payment_id)
            if pay_entity and pay_entity.get("status") in ("captured", "authorized"):
                is_verified = True
        except Exception as api_err:
            logger.warning("Could not fetch payment %s from Razorpay API: %s", payment_id, api_err)

    if not is_verified:
        try:
            ord_entity = _client().order.fetch(order_id)
            if ord_entity and ord_entity.get("status") == "paid":
                is_verified = True
        except Exception as ord_err:
            logger.warning("Could not fetch order %s from Razorpay API: %s", order_id, ord_err)

    if is_verified:
        from app.tenancy.scoping import tenant_context
        payment = Payment.query.filter_by(gateway_order_id=order_id).first()
        effective_org_id = org_id or (payment.organization_id if payment else None)
        if effective_org_id:
            with tenant_context(effective_org_id):
                confirm_payment(order_id, "success", transaction_id=payment_id)
        else:
            confirm_payment(order_id, "success", transaction_id=payment_id)
        return True

    return False


def sync_booking_payment(booking: Booking) -> bool:
    """
    Checks Razorpay gateway for payment status of a pending booking.
    If paid, confirms the payment and updates the booking to 'booked'.
    """
    if booking.status != "pending":
        return False

    payment = Payment.query.filter_by(
        booking_id=booking.id,
        status="initiated",
    ).order_by(Payment.id.desc()).first()

    if not payment or not payment.gateway_order_id:
        return False

    order_id = payment.gateway_order_id
    try:
        client = _client()
        payments_data = client.order.payments(order_id)
        items = payments_data.get("items", []) if isinstance(payments_data, dict) else []
        captured = next(
            (p for p in items if p.get("status") in ("captured", "authorized")),
            None,
        )

        if captured:
            tx_id = captured.get("id")
            from app.tenancy.scoping import tenant_context
            with tenant_context(booking.organization_id):
                confirm_payment(order_id, "success", transaction_id=tx_id)
            return True

        ord_info = client.order.fetch(order_id)
        if ord_info and ord_info.get("status") == "paid":
            tx_id = items[0].get("id") if items else None
            from app.tenancy.scoping import tenant_context
            with tenant_context(booking.organization_id):
                confirm_payment(order_id, "success", transaction_id=tx_id)
            return True

    except Exception as exc:
        logger.warning(
            "Could not query Razorpay order %s for booking %s: %s",
            order_id,
            booking.id,
            exc,
        )

    return False


def confirm_manual_booking(booking: Booking, signer) -> Booking:
    """
    Confirms and signs off on a pending manual booking.
    Decrements slots under lock.
    """
    if booking.booking_channel != "manual":
        raise ValueError("Only manual bookings can be signed off via confirm_manual_booking.")

    if booking.status != "pending" or booking.manual_sign_off_at is not None:
        return booking  # Idempotent

    trek = Trek.query.with_for_update().filter_by(id=booking.trek_id).first()
    if not trek or trek.status != "open":
        raise SlotUnavailableError("Trek is not currently open for bookings.")

    slot_count = booking.slot_count or 1
    if trek.available_slots < slot_count:
        raise SlotUnavailableError(
            f"Insufficient slots remaining ({trek.available_slots} available, {slot_count} requested)."
        )

    _decrement_slots(trek, slot_count)
    booking.status = "booked"
    booking.payment_status = "paid"
    booking.manual_sign_off_by = signer.id
    now = datetime.now(timezone.utc)
    booking.manual_sign_off_at = now

    # Update offline payment
    offline_payment = Payment.query.filter_by(booking_id=booking.id, channel="offline").first()
    if offline_payment:
        offline_payment.status = "success"
        offline_payment.payment_date = now

    db.session.commit()

    # Post-commit actions
    try:
        invoice_service.generate_invoice(booking)
    except Exception as e:
        logger.error("Invoice generation failed for manual booking %s: %s", booking.id, e)

    try:
        notification_service.send_booking_confirmed_email(booking)
    except Exception:
        pass

    log = AuditLog(
        organization_id=booking.organization_id,
        user_id=signer.id,
        action="booking.manual_sign_off",
        target_type="booking",
        target_id=booking.id,
        details=f'{{"booking_id": {booking.id}, "signer_id": {signer.id}}}',
    )
    db.session.add(log)
    db.session.commit()

    return booking


def cancel_booking(
    booking: Booking,
    actor,
    reason: str = "customer_requested",
    note: Optional[str] = None,
) -> Booking:
    """
    Cancels a booking. Handles slot replenishment, refund policy calculation,
    and waitlist promotion.
    """
    if booking.status == "pending":
        # Cancellation of pending booking (no slots held yet)
        booking.status = "cancelled"
        booking.cancellation_reason = reason
        booking.cancelled_by = getattr(actor, "id", None)
        for p in Payment.query.filter_by(booking_id=booking.id, status="initiated").all():
            p.status = "failed"
        db.session.commit()
        return booking

    if booking.status != "booked":
        raise ValueError("Only 'booked' or 'pending' bookings can be cancelled.")

    trek = Trek.query.with_for_update().filter_by(id=booking.trek_id).first()
    if trek and trek.status == "completed":
        raise refund_service.RefundNotAllowedError("Cannot cancel bookings for completed treks.")

    slot_count = booking.slot_count or 1
    pct = refund_service.calculate_refund(booking, reason)

    booking.status = "cancelled"
    booking.cancellation_reason = reason
    booking.cancelled_by = getattr(actor, "id", None)
    booking.refund_percentage_applied = pct

    if pct > 0:
        booking.payment_status = "refunded"
        if hasattr(booking, "payment") and booking.payment:
            booking.payment.status = "refunded"
        for p in Payment.query.filter_by(booking_id=booking.id).all():
            if p.status in ("paid", "success"):
                p.status = "refunded"
    else:
        booking.payment_status = "paid"  # 0% refund keeps paid status

    if trek:
        _increment_slots(trek, slot_count)

    db.session.commit()

    # Trigger waitlist promotion
    if trek:
        waitlist_service.on_slots_freed(trek)

    # Send cancellation communication
    if reason == "staff_force_cancel":
        notification_service.send_force_cancel_email(booking, note or "Operational adjustments")

    return booking


def expire_stale_bookings() -> int:
    """
    Sweeps across all tenants to expire stale ONLINE bookings (>= 30 mins old).
    Pending MANUAL bookings are deliberately excluded (awaiting admin sign-off).
    """
    with tenant_bypass():
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
        stale = Booking.query.filter(
            Booking.status == "pending",
            Booking.booking_channel == "online",
            Booking.booking_date < cutoff,
        ).all()

        count = 0
        for booking in stale:
            booking.status = "cancelled"
            for payment in Payment.query.filter_by(booking_id=booking.id, status="initiated").all():
                payment.status = "failed"
            count += 1

        if count:
            db.session.commit()
        return count
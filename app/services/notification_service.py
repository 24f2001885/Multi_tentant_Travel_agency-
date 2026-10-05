
import logging

from flask import render_template
from flask_mail import Message

from app.extensions import mail

logger = logging.getLogger(__name__)

def send_otp_reset_email(user, raw_token):
    try:
        msg = Message(
            subject="Reset your TMA password",
            recipients=[user.email],
            html=render_template("email/otp_reset.html", user=user, raw_token=raw_token),
        )
        mail.send(msg)
    except Exception as exc:  # noqa: BLE001 — a mail outage must never 500 the request
        logger.error("Failed to send OTP reset email to %s: %s", user.email, exc)

def send_staff_approved_email(user):
    try:
        msg = Message(
            subject="Your Trek Staff account has been approved",
            recipients=[user.email],
            html=render_template("email/staff_approved.html", user=user),
        )
        mail.send(msg)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send staff-approved email to %s: %s", user.email, exc)

def send_booking_confirmed_email(booking):
    try:
        msg = Message(
            subject=f"Booking Confirmed: {booking.trek.name}",
            recipients=[booking.user.email],
            html=render_template("email/booking_confirmed.html", booking=booking),
        )
        mail.send(msg)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send booking confirmed email to %s: %s", booking.user.email, exc)

def send_ticket_response_email(ticket):
    try:
        msg = Message(
            subject=f"Update on your support ticket: {ticket.subject}",
            recipients=[ticket.user.email],
            html=render_template("email/ticket_response.html", ticket=ticket),
        )
        mail.send(msg)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send ticket response email to %s: %s", ticket.user.email, exc)
"""
Backup and Disaster Recovery service (TDR v2 §4.6, §15; PDR v2 §5.14; Milestone P13).
Handles tenant self-serve data exports, platform-level backups, and retention sweeps.
"""

from datetime import datetime, timezone
import gzip
import json
import os
from typing import Any, Dict, List, Optional
import uuid

from flask import current_app
from sqlalchemy import select

from app.extensions import db
from app.models.booking import Booking
from app.models.booking_participant import BookingParticipant
from app.models.call_request import CallRequest
from app.models.coupon import Coupon
from app.models.coupon_redemption import CouponRedemption
from app.models.invoice import Invoice
from app.models.itinerary_element import ItineraryElement
from app.models.organization import Organization
from app.models.payment import Payment
from app.models.platform_access_log import PlatformAccessLog
from app.models.refund_policy import RefundPolicy
from app.models.tenant_backup import TenantBackup
from app.models.ticket import Ticket
from app.models.ticket_message import TicketMessage
from app.models.trek import Trek
from app.models.trek_itinerary_day import TrekItineraryDay
from app.models.trek_media import TrekMedia
from app.models.trek_review import TrekReview
from app.models.user import User
from app.models.waitlist_entry import WaitlistEntry
from app.tenancy.scoping import tenant_bypass, tenant_context


def _get_backup_dir(subfolder: str) -> str:
    base = os.path.join(current_app.instance_path, "backups", str(subfolder))
    os.makedirs(base, exist_ok=True)
    return base


def create_tenant_backup(
    organization_id: uuid.UUID,
    triggered_by_user_id: Optional[int] = None,
    triggered_by_platform_admin_id: Optional[uuid.UUID] = None,
) -> TenantBackup:
    """
    Exports all tenant-scoped data into a compressed, timestamped JSON archive.
    Can be triggered self-serve by Tenant Admin or by Platform Admin.
    """
    backup_id = uuid.uuid4()
    backup_dir = _get_backup_dir(str(organization_id))
    file_path = os.path.join(backup_dir, f"{backup_id}.json.gz")

    with tenant_context(organization_id):
        org = db.session.get(Organization, organization_id)
        if not org:
            raise ValueError(f"Organization {organization_id} not found.")

        # Serialize Organization details
        export_data: Dict[str, Any] = {
            "version": "2.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "organization": {
                "id": str(org.id),
                "name": org.name,
                "status": org.status,
                "created_at": org.created_at.isoformat() if org.created_at else None,
            },
            "treks": [],
            "bookings": [],
            "coupons": [],
            "refund_policies": [],
            "waitlist": [],
            "tickets": [],
            "reviews": [],
            "call_requests": [],
        }

        # Treks & Itineraries & Media
        treks = Trek.query.all()
        for t in treks:
            t_data = {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "location": t.location,
                "difficulty": t.difficulty,
                "duration_days": t.duration_days,
                "total_slots": t.total_slots,
                "available_slots": t.available_slots,
                "price": float(t.price) if t.price is not None else 0.0,
                "status": t.status,
                "start_date": t.start_date.isoformat() if t.start_date else None,
                "end_date": t.end_date.isoformat() if t.end_date else None,
                "itinerary_days": [],
                "media": [],
            }
            for day in t.itinerary_days:
                d_data = {
                    "day_number": day.day_number,
                    "title": day.title,
                    "description": day.description,
                    "elements": [
                        {
                            "sequence": el.sequence,
                            "element_type": el.element_type,
                            "title": el.title,
                            "description": el.description,
                            "time_of_day": el.time_of_day,
                        }
                        for el in day.elements
                    ],
                }
                t_data["itinerary_days"].append(d_data)
            for m in t.media:
                t_data["media"].append({
                    "asset_url": m.asset_url,
                    "caption": m.caption,
                    "sort_order": m.sort_order,
                })
            export_data["treks"].append(t_data)

        # Bookings & Participants & Invoices
        bookings = Booking.query.all()
        for b in bookings:
            b_data = {
                "id": b.id,
                "user_id": b.user_id,
                "trek_id": b.trek_id,
                "slots": b.slots,
                "total_price": float(b.total_price) if b.total_price is not None else 0.0,
                "status": b.status,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "participants": [
                    {
                        "full_name": p.full_name,
                        "email": p.email,
                        "phone_number": p.phone_number,
                        "age": p.age,
                        "gender": p.gender,
                        "is_primary": p.is_primary,
                    }
                    for p in b.participants
                ],
            }
            if hasattr(b, "invoice") and b.invoice:
                b_data["invoice"] = {
                    "invoice_number": b.invoice.invoice_number,
                    "tax_amount": float(b.invoice.tax_amount) if b.invoice.tax_amount else 0.0,
                    "total_amount": float(b.invoice.total_amount) if b.invoice.total_amount else 0.0,
                }
            export_data["bookings"].append(b_data)

        # Coupons
        for c in Coupon.query.all():
            export_data["coupons"].append({
                "code": c.code,
                "discount_type": c.discount_type,
                "discount_value": float(c.discount_value),
                "is_active": c.is_active,
                "used_count": c.used_count,
            })

        # Tickets & Thread Messages
        for tk in Ticket.query.all():
            export_data["tickets"].append({
                "id": tk.id,
                "user_id": tk.user_id,
                "type": tk.type,
                "subject": tk.subject,
                "message": tk.message,
                "status": tk.status,
                "created_at": tk.created_at.isoformat() if tk.created_at else None,
                "messages": [
                    {
                        "sender_id": msg.sender_id,
                        "message": msg.message,
                        "created_at": msg.created_at.isoformat() if msg.created_at else None,
                    }
                    for msg in tk.messages
                ],
            })

        # Reviews
        for rev in TrekReview.query.all():
            export_data["reviews"].append({
                "trek_id": rev.trek_id,
                "user_id": rev.user_id,
                "rating": rev.rating,
                "comment": rev.comment,
                "created_at": rev.created_at.isoformat() if rev.created_at else None,
            })

    # Compress and write to disk
    json_bytes = json.dumps(export_data, indent=2).encode("utf-8")
    with gzip.open(file_path, "wb") as gz_file:
        gz_file.write(json_bytes)

    file_size = os.path.getsize(file_path)

    # Record in database
    with tenant_bypass():
        backup_record = TenantBackup(
            id=backup_id,
            organization_id=organization_id,
            triggered_by_user_id=triggered_by_user_id,
            triggered_by_platform_admin_id=triggered_by_platform_admin_id,
            storage_location=file_path,
            file_size_bytes=file_size,
            status="completed",
            backup_type="tenant_export",
        )
        db.session.add(backup_record)

        if triggered_by_platform_admin_id:
            PlatformAccessLog.record(
                platform_admin_id=triggered_by_platform_admin_id,
                organization_id=organization_id,
                action="export_tenant_backup",
            )

        db.session.commit()

    return backup_record


def create_platform_backup(platform_admin_id: uuid.UUID) -> TenantBackup:
    """
    Creates a full platform backup across all tenant organizations.
    """
    backup_id = uuid.uuid4()
    backup_dir = _get_backup_dir("platform")
    file_path = os.path.join(backup_dir, f"{backup_id}.json.gz")

    platform_data: Dict[str, Any] = {
        "version": "2.0",
        "scope": "full_platform",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "organizations": [],
    }

    with tenant_bypass():
        orgs = Organization.query.all()
        for o in orgs:
            platform_data["organizations"].append({
                "id": str(o.id),
                "name": o.name,
                "status": o.status,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            })

    json_bytes = json.dumps(platform_data, indent=2).encode("utf-8")
    with gzip.open(file_path, "wb") as gz_file:
        gz_file.write(json_bytes)

    file_size = os.path.getsize(file_path)

    with tenant_bypass():
        backup_record = TenantBackup(
            id=backup_id,
            organization_id=None,  # Full platform
            triggered_by_platform_admin_id=platform_admin_id,
            storage_location=file_path,
            file_size_bytes=file_size,
            status="completed",
            backup_type="full_platform",
        )
        db.session.add(backup_record)
        PlatformAccessLog.record(
            platform_admin_id=platform_admin_id,
            organization_id=orgs[0].id if orgs else uuid.uuid4(),
            action="full_platform_backup",
        )
        db.session.commit()

    return backup_record


def purge_expired_backups() -> int:
    """
    Removes backup files whose retention window has passed.
    """
    purged_count = 0
    now = datetime.now(timezone.utc)
    with tenant_bypass():
        expired = TenantBackup.query.filter(TenantBackup.expires_at < now).all()
        for b in expired:
            if os.path.exists(b.storage_location):
                try:
                    os.remove(b.storage_location)
                except OSError:
                    pass
            db.session.delete(b)
            purged_count += 1
        if purged_count:
            db.session.commit()
    return purged_count

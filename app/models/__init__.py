from app.models.audit_log import AuditLog
from app.models.booking import Booking
from app.models.call_request import CallRequest
from app.models.capability import Capability
from app.models.domain import Domain
from app.models.organization import Organization
from app.models.password_reset_token import PasswordResetToken
from app.models.payment import Payment
from app.models.platform_access_log import PlatformAccessLog
from app.models.platform_admin import PlatformAdmin
from app.models.role import Role, role_capabilities
from app.models.staff_profile import StaffProfile
from app.models.ticket import Ticket
from app.models.trek import Trek
from app.models.user import User

from app.models.booking_participant import BookingParticipant
from app.models.refund_policy import RefundPolicy
from app.models.coupon import Coupon
from app.models.coupon_redemption import CouponRedemption
from app.models.invoice import Invoice
from app.models.waitlist_entry import WaitlistEntry
from app.models.trek_itinerary_day import TrekItineraryDay
from app.models.itinerary_element import ItineraryElement
from app.models.trek_media import TrekMedia
from app.models.trek_review import TrekReview
from app.models.notification_consent import NotificationConsent
from app.models.notification_preference import NotificationPreference
from app.models.message_campaign import MessageCampaign
from app.models.message_campaign_recipient import MessageCampaignRecipient
from app.models.ticket_message import TicketMessage
from app.models.tenant_backup import TenantBackup

__all__ = [
    "User",
    "StaffProfile",
    "Trek",
    "Booking",
    "Payment",
    "Ticket",
    "PasswordResetToken",
    "AuditLog",
    "Organization",
    "Domain",
    "CallRequest",
    "PlatformAdmin",
    "PlatformAccessLog",
    "Capability",
    "Role",
    "role_capabilities",
    "BookingParticipant",
    "RefundPolicy",
    "Coupon",
    "CouponRedemption",
    "Invoice",
    "WaitlistEntry",
    "TrekItineraryDay",
    "ItineraryElement",
    "TrekMedia",
    "TrekReview",
    "NotificationConsent",
    "NotificationPreference",
    "MessageCampaign",
    "MessageCampaignRecipient",
    "TicketMessage",
    "TenantBackup",
]

# print(f"[DEBUG] models package exports {len(__all__)}")


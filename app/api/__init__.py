
from flask import Blueprint, jsonify, request
from flask_restful import Api

from app.extensions import csrf

api_bp = Blueprint("api", __name__, url_prefix="/api")
csrf.exempt(api_bp)
api = Api(api_bp)

from app.api.auth_resource import (  # noqa: E402
    ChangePasswordResource,
    ForgotPasswordResource,
    LoginResource,
    LogoutResource,
    RegisterResource,
    ResetPasswordResource,
)
from app.api.bookings_resource import BookingListResource, BookingResource  # noqa: E402
from app.api.payments_resource import (  # noqa: E402
    PaymentListForBookingResource,
    PaymentWebhookResource,
)
from app.api.staff_resource import (  # noqa: E402
    StaffApproveResource,
    StaffBlacklistResource,
    StaffListResource,
)
from app.api.stats_resource import (  # noqa: E402
    AdminStatsResource,
    StaffStatsResource,
    TrekkerStatsResource,
)
from app.api.tickets_resource import (  # noqa: E402
    TicketListResource,
    TicketResolveResource,
    TicketRespondResource,
    TicketResource,
)
from app.api.treks_resource import (  # noqa: E402
    TrekAssignStaffResource,
    TrekListResource,
    TrekResource,
)
from app.api.serializers import HashidField, encode_id, decode_id  # noqa: E402
from app.api.users_resource import UserListResource, UserResource  # noqa: E402

api.add_resource(PaymentWebhookResource, "/payments/webhook")
api.add_resource(RegisterResource, "/auth/register")
api.add_resource(LoginResource, "/auth/login")
api.add_resource(LogoutResource, "/auth/logout")
api.add_resource(ForgotPasswordResource, "/auth/forgot-password")
api.add_resource(ResetPasswordResource, "/auth/reset-password")
api.add_resource(ChangePasswordResource, "/auth/change-password")
api.add_resource(UserListResource, "/users")
api.add_resource(UserResource, "/users/<hashid:id>")
api.add_resource(StaffListResource, "/staff")
api.add_resource(StaffApproveResource, "/staff/<hashid:id>/approve")
api.add_resource(StaffBlacklistResource, "/staff/<hashid:id>/blacklist")
api.add_resource(TrekListResource, "/treks")
api.add_resource(TrekResource, "/treks/<hashid:id>")
api.add_resource(TrekAssignStaffResource, "/treks/<hashid:id>/assign-staff")
api.add_resource(BookingListResource, "/bookings")
api.add_resource(BookingResource, "/bookings/<hashid:id>")
api.add_resource(PaymentListForBookingResource, "/payments/<hashid:booking_id>")
api.add_resource(TicketListResource, "/tickets")
api.add_resource(TicketResource, "/tickets/<hashid:id>")
api.add_resource(TicketRespondResource, "/tickets/<hashid:id>/respond")
api.add_resource(TicketResolveResource, "/tickets/<hashid:id>/resolve")
api.add_resource(AdminStatsResource, "/stats/admin")
api.add_resource(StaffStatsResource, "/stats/staff/<hashid:staff_id>")
api.add_resource(TrekkerStatsResource, "/stats/trekker/<hashid:user_id>")


def _is_api_request():
    return request.path.startswith("/api/")


def _json_error(code, message, status_code):
    response = jsonify({"error": {"code": code, "message": message}})
    response.status_code = status_code
    return response


@api_bp.app_errorhandler(400)
def api_bad_request(error):
    return _json_error("BAD_REQUEST", "The request was malformed.", 400) if _is_api_request() else error


@api_bp.app_errorhandler(401)
def api_unauthorized(error):
    return _json_error("UNAUTHORIZED", "Authentication is required.", 401) if _is_api_request() else error


@api_bp.app_errorhandler(403)
def api_forbidden(error):
    return _json_error("FORBIDDEN", "You do not have access to this resource.", 403) if _is_api_request() else error


@api_bp.app_errorhandler(404)
def api_not_found(error):
    return _json_error("NOT_FOUND", "The requested resource was not found.", 404) if _is_api_request() else error


@api_bp.app_errorhandler(409)
def api_conflict(error):
    return _json_error("CONFLICT", "The request conflicts with the current state.", 409) if _is_api_request() else error


@api_bp.app_errorhandler(422)
def api_unprocessable(error):
    return _json_error("VALIDATION_ERROR", "The request could not be validated.", 422) if _is_api_request() else error


@api_bp.app_errorhandler(500)
def api_server_error(error):
    return _json_error("SERVER_ERROR", "An unexpected error occurred.", 500) if _is_api_request() else error
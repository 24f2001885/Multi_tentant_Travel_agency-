
from collections import defaultdict

from flask import abort
from flask_login import current_user, login_required
from flask_restful import Resource

from app.auth.decorators import role_required
from app.models import Booking, StaffProfile, Trek, User
from app.services import history_service


class AdminStatsResource(Resource):
    method_decorators = [role_required("admin")]

    def get(self):
        counts = history_service.get_dashboard_counts(current_user)
        return {
            "total_treks": counts.get("total_treks", Trek.query.count()),
            "total_users": counts.get("total_users", User.query.count()),
            "total_staff": counts.get("total_staff", StaffProfile.query.count()),
            "total_bookings": counts.get("total_bookings", Booking.query.count()),
        }, 200


class StaffStatsResource(Resource):
    method_decorators = [login_required]

    def get(self, staff_id):
        is_self = (
            current_user.role == "staff"
            and current_user.staff_profile is not None
            and current_user.staff_profile.id == staff_id
        )
        if current_user.role != "admin" and not is_self:
            abort(403)

        profile = StaffProfile.query.get(staff_id)
        if profile is None:
            abort(404)

        assigned = []
        for trek in Trek.query.filter_by(assigned_staff_id=profile.id).all():
            participant_count = Booking.query.filter(
                Booking.trek_id == trek.id, Booking.status.in_(("pending", "booked"))
            ).count()
            assigned.append(
                {"trek_id": trek.id, "name": trek.name, "participant_count": participant_count}
            )

        return {"assigned_treks": assigned}, 200


class TrekkerStatsResource(Resource):
    method_decorators = [login_required]

    def get(self, user_id):
        if current_user.role != "admin" and current_user.id != user_id:
            abort(403)

        user = User.query.get(user_id)
        if user is None:
            abort(404)

        bookings = history_service.get_booking_history(user)
        monthly_counts = defaultdict(int)
        for booking in bookings:
            key = booking.booking_date.strftime("%Y-%m")
            monthly_counts[key] += 1

        return {
            "booking_history": [
                {
                    "id": b.id,
                    "trek_id": b.trek_id,
                    "status": b.status,
                    "payment_status": b.payment_status,
                }
                for b in bookings
            ],
            "monthly_booking_counts": dict(monthly_counts),
        }, 200
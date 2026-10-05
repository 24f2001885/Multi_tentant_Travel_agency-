
from flask import request
from flask_login import current_user
from flask_restful import Resource
from marshmallow import Schema, fields

from app.api.serializers import HashidField, decode_id
from app.extensions import db
from app.models import AuditLog, Booking, StaffProfile, Trek
from app.services.trek_service import InvalidTransitionError, transition_status


def _error(code, message, status_code):
    return {"error": {"code": code, "message": message}}, status_code


class TrekSchema(Schema):
    id = HashidField()
    name = fields.Str()
    location = fields.Str()
    difficulty = fields.Str()
    duration_days = fields.Int()
    total_slots = fields.Int()
    available_slots = fields.Int()
    price = fields.Decimal(as_string=True)
    assigned_staff_id = HashidField(allow_none=True)
    status = fields.Str()
    start_date = fields.Date()
    end_date = fields.Date()


class TrekListResource(Resource):
    def get(self):
        query = Trek.query

        difficulty = request.args.get("difficulty")
        if difficulty:
            query = query.filter(Trek.difficulty == difficulty)

        location = request.args.get("location")
        if location:
            query = query.filter(Trek.location.ilike(f"%{location}%"))

        is_privileged = current_user.is_authenticated and current_user.role in ("admin", "staff")
        if is_privileged:
            status = request.args.get("status")
            if status:
                query = query.filter(Trek.status == status)
        else:
            # Trekker/anonymous callers only ever see open, bookable treks,
            # regardless of any status query param supplied.
            query = query.filter(Trek.status == "open", Trek.available_slots > 0)

        items = query.all()
        return {"items": TrekSchema(many=True).dump(items), "total": len(items)}, 200

    def post(self):
        if not current_user.is_authenticated or current_user.role != "admin":
            return _error("FORBIDDEN", "Admin access required.", 403)

        payload = request.get_json(silent=True) or {}
        required = [
            "name", "location", "difficulty", "duration_days",
            "total_slots", "price", "start_date", "end_date",
        ]
        missing = [f for f in required if f not in payload]
        if missing:
            return _error("VALIDATION_ERROR", f"Missing fields: {', '.join(missing)}", 422)

        trek = Trek(
            name=payload["name"],
            location=payload["location"],
            difficulty=payload["difficulty"],
            duration_days=payload["duration_days"],
            total_slots=payload["total_slots"],
            available_slots=payload["total_slots"],
            price=payload["price"],
            start_date=payload["start_date"],
            end_date=payload["end_date"],
            status="pending",  # never accept a caller-supplied status on create
        )
        db.session.add(trek)
        db.session.commit()
        db.session.add(AuditLog.record(current_user.id, "trek.create", "trek", trek.id))
        db.session.commit()
        return TrekSchema().dump(trek), 201


class TrekResource(Resource):
    def get(self, id):
        trek = Trek.query.get(id)
        if trek is None:
            return _error("NOT_FOUND", "Trek not found.", 404)
        return TrekSchema().dump(trek), 200

    def put(self, id):
        if not current_user.is_authenticated:
            return _error("UNAUTHORIZED", "Login required.", 401)

        trek = Trek.query.get(id)
        if trek is None:
            return _error("NOT_FOUND", "Trek not found.", 404)

        payload = request.get_json(silent=True) or {}

        if current_user.role == "admin":
            before = TrekSchema().dump(trek)
            for field in (
                "name", "location", "difficulty", "duration_days", "total_slots",
                "available_slots", "price", "status",
                "start_date", "end_date",
            ):
                if field in payload:
                    setattr(trek, field, payload[field])
            if "assigned_staff_id" in payload:
                try:
                    trek.assigned_staff_id = decode_id(payload["assigned_staff_id"], kind="assigned_staff_id")
                except ValueError as err:
                    return _error("VALIDATION_ERROR", str(err), 422)
            db.session.commit()
            after = TrekSchema().dump(trek)
            db.session.add(AuditLog.record(
                current_user.id, "trek.update", "trek", trek.id,
                details=str({"before": before, "after": after}),
            ))
            db.session.commit()
            return TrekSchema().dump(trek), 200

        if current_user.role == "staff":
            staff_profile = current_user.staff_profile
            if staff_profile is None or trek.assigned_staff_id != staff_profile.id:
                return _error("FORBIDDEN", "You are not assigned to this trek.", 403)

            if "available_slots" in payload:
                trek.available_slots = payload["available_slots"]
            if "status" in payload:
                try:
                    transition_status(trek, payload["status"], actor=current_user)
                except InvalidTransitionError as exc:
                    return _error("INVALID_TRANSITION", str(exc), 409)
            db.session.commit()
            return TrekSchema().dump(trek), 200

        return _error("FORBIDDEN", "You do not have access to modify this trek.", 403)

    def delete(self, id):
        if not current_user.is_authenticated or current_user.role != "admin":
            return _error("FORBIDDEN", "Admin access required.", 403)

        trek = Trek.query.get(id)
        if trek is None:
            return _error("NOT_FOUND", "Trek not found.", 404)

        active_booking = Booking.query.filter(
            Booking.trek_id == trek.id, Booking.status != "cancelled"
        ).first()
        if active_booking is not None:
            return _error(
                "TREK_HAS_ACTIVE_BOOKINGS", "This trek has active bookings and cannot be deleted.", 409
            )

        db.session.delete(trek)
        db.session.add(AuditLog.record(current_user.id, "trek.delete", "trek", id))
        db.session.commit()
        return "", 204


class TrekAssignStaffResource(Resource):
    def put(self, id):
        if not current_user.is_authenticated or current_user.role != "admin":
            return _error("FORBIDDEN", "Admin access required.", 403)

        trek = Trek.query.get(id)
        if trek is None:
            return _error("NOT_FOUND", "Trek not found.", 404)

        payload = request.get_json(silent=True) or {}
        raw_staff_id = payload.get("staff_id")
        try:
            staff_id = decode_id(raw_staff_id, kind="staff_id")
        except ValueError as err:
            return _error("VALIDATION_ERROR", str(err), 422)
        profile = StaffProfile.query.get(staff_id) if staff_id else None
        if profile is None or profile.approval_status != "approved":
            return _error("INVALID_STAFF", "Staff member must exist and be approved.", 422)

        trek.assigned_staff_id = profile.id
        db.session.add(AuditLog.record(current_user.id, "trek.assign_staff", "trek", trek.id))
        db.session.commit()
        return TrekSchema().dump(trek), 200

from datetime import datetime, timezone

from flask import request
from flask_login import current_user
from flask_restful import Resource
from marshmallow import Schema, fields

from app.api.serializers import HashidField
from app.auth.decorators import role_required
from app.extensions import db
from app.models import AuditLog, StaffProfile
from app.services import auth_service, notification_service


def _error(code, message, status_code):
    return {"error": {"code": code, "message": message}}, status_code


class StaffProfileSchema(Schema):
    id = HashidField()
    user_id = HashidField()
    contact_details = fields.Str(allow_none=True)
    approval_status = fields.Str()
    approved_by_admin_id = HashidField(allow_none=True)
    approved_at = fields.DateTime(allow_none=True)


class StaffListResource(Resource):
    method_decorators = [role_required("admin")]

    def get(self):
        query = StaffProfile.query
        approval_status = request.args.get("approval_status")
        if approval_status:
            query = query.filter(StaffProfile.approval_status == approval_status)
        return {"items": StaffProfileSchema(many=True).dump(query.all())}, 200


class StaffApproveResource(Resource):
    method_decorators = [role_required("admin")]

    def put(self, id):
        profile = StaffProfile.query.get(id)
        if profile is None:
            return _error("NOT_FOUND", "Staff profile not found.", 404)

        profile.approval_status = "approved"
        profile.approved_by_admin_id = current_user.id
        profile.approved_at = datetime.now(timezone.utc)
        auth_service.issue_staff_default_password(profile.user)
        db.session.add(
            AuditLog.record(current_user.id, "staff.approve", "staff_profile", profile.id)
        )
        db.session.commit()

        notification_service.send_staff_approved_email(profile.user)
        return StaffProfileSchema().dump(profile), 200


class StaffBlacklistResource(Resource):
    method_decorators = [role_required("admin")]

    def put(self, id):
        profile = StaffProfile.query.get(id)
        if profile is None:
            return _error("NOT_FOUND", "Staff profile not found.", 404)

        profile.approval_status = "blacklisted"
        profile.user.is_active = False
        db.session.add(
            AuditLog.record(current_user.id, "staff.blacklist", "staff_profile", profile.id)
        )
        db.session.commit()
        return StaffProfileSchema().dump(profile), 200
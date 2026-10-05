
from flask import request
from flask_login import current_user
from flask_restful import Resource
from marshmallow import Schema, fields

from app.auth.decorators import role_required
from app.extensions import db
from app.models import AuditLog, User


def _error(code, message, status_code):
    return {"error": {"code": code, "message": message}}, status_code


def _paginate(query):
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(request.args.get("per_page", 20))
    except (TypeError, ValueError):
        per_page = 20
    per_page = max(1, min(per_page, 100))
    return query.paginate(page=page, per_page=per_page, error_out=False)


class UserSchema(Schema):
    id = fields.Int()
    name = fields.Str()
    email = fields.Str()
    role = fields.Str()
    is_active = fields.Bool()
    created_at = fields.DateTime()


class UserListResource(Resource):
    method_decorators = [role_required("admin")]

    def get(self):
        query = User.query

        role = request.args.get("role")
        if role:
            query = query.filter(User.role == role)

        search = request.args.get("search")
        if search:
            like = f"%{search}%"
            query = query.filter(db.or_(User.name.ilike(like), User.email.ilike(like)))

        is_active = request.args.get("is_active")
        if is_active is not None:
            query = query.filter(User.is_active == (is_active.lower() == "true"))

        pagination = _paginate(query.order_by(User.created_at.desc()))
        return {
            "items": UserSchema(many=True).dump(pagination.items),
            "total": pagination.total,
            "page": pagination.page,
        }, 200


class UserResource(Resource):
    method_decorators = [role_required("admin")]

    def get(self, id):
        user = User.query.get(id)
        if user is None:
            return _error("NOT_FOUND", "User not found.", 404)
        return UserSchema().dump(user), 200

    def put(self, id):
        user = User.query.get(id)
        if user is None:
            return _error("NOT_FOUND", "User not found.", 404)

        payload = request.get_json(silent=True) or {}
        if set(payload.keys()) - {"is_active"}:
            return _error(
                "VALIDATION_ERROR", "Only 'is_active' may be updated via this endpoint.", 422
            )
        if "is_active" not in payload or not isinstance(payload["is_active"], bool):
            return _error("VALIDATION_ERROR", "'is_active' (boolean) is required.", 422)

        user.is_active = payload["is_active"]
        action = "user.reactivate" if user.is_active else "user.deactivate"
        db.session.add(AuditLog.record(current_user.id, action, "user", user.id))
        db.session.commit()
        return UserSchema().dump(user), 200
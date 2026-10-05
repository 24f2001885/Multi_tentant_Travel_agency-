
from flask import request
from flask_login import current_user, login_required, login_user, logout_user
from flask_restful import Resource
from marshmallow import Schema, ValidationError, fields, validate

from app.api.serializers import encode_id
from app.extensions import db, limiter
from app.services import auth_service
from app.services.auth_service import DuplicateEmailError


def _error(code, message, status_code):
    return {"error": {"code": code, "message": message}}, status_code


class RegisterSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=120))
    email = fields.Email(required=True)
    password = fields.Str(required=True, validate=validate.Length(min=8))
    role = fields.Str(required=True, validate=validate.OneOf(["trekker", "staff"]))
    contact_details = fields.Str(required=False, allow_none=True)


class LoginSchema(Schema):
    email = fields.Email(required=True)
    password = fields.Str(required=True)


class ForgotPasswordSchema(Schema):
    email = fields.Email(required=True)


class ResetPasswordSchema(Schema):
    token = fields.Str(required=True)
    new_password = fields.Str(required=True, validate=validate.Length(min=8))


class ChangePasswordSchema(Schema):
    current_password = fields.Str(required=True)
    new_password = fields.Str(required=True, validate=validate.Length(min=8))


class RegisterResource(Resource):
    def post(self):
        payload = request.get_json(silent=True) or {}
        try:
            data = RegisterSchema().load(payload)
        except ValidationError as err:
            return _error("VALIDATION_ERROR", str(err.messages), 422)

        try:
            user = auth_service.register_user(
                name=data["name"],
                email=data["email"],
                password=data["password"],
                role=data["role"],
                contact_details=data.get("contact_details"),
            )
        except DuplicateEmailError:
            return _error("EMAIL_TAKEN", "An account with this email already exists.", 409)

        status = "active" if user.role == "trekker" else "pending_approval"
        return {"id": encode_id(user.id), "role": user.role, "status": status}, 201


class LoginResource(Resource):
    method_decorators = [limiter.limit("5/minute")]

    def post(self):
        payload = request.get_json(silent=True) or {}
        try:
            data = LoginSchema().load(payload)
        except ValidationError as err:
            return _error("VALIDATION_ERROR", str(err.messages), 422)

        user = auth_service.authenticate(data["email"], data["password"])
        if user is None:
            return _error("INVALID_CREDENTIALS", "Invalid email or password.", 401)

        login_user(user)
        redirect = {"admin": "/admin", "staff": "/staff", "trekker": "/trekker"}[user.role]
        return {
            "user": {
                "id": encode_id(user.id),
                "name": user.name,
                "email": user.email,
                "role": user.role,
            },
            "redirect": redirect,
        }, 200


class LogoutResource(Resource):
    method_decorators = [login_required]

    def post(self):
        logout_user()
        return "", 204


class ForgotPasswordResource(Resource):
    method_decorators = [limiter.limit("5/minute")]

    def post(self):
        payload = request.get_json(silent=True) or {}
        try:
            data = ForgotPasswordSchema().load(payload)
        except ValidationError as err:
            return _error("VALIDATION_ERROR", str(err.messages), 422)

        auth_service.request_password_reset(data["email"])
        # same response either way, so we don't leak which emails are registered
        return {"message": "If that email exists, a reset link was sent."}, 202


class ResetPasswordResource(Resource):
    method_decorators = [limiter.limit("5/minute")]

    def post(self):
        payload = request.get_json(silent=True) or {}
        try:
            data = ResetPasswordSchema().load(payload)
        except ValidationError as err:
            return _error("VALIDATION_ERROR", str(err.messages), 422)

        ok = auth_service.reset_password(data["token"], data["new_password"])
        if not ok:
            return _error(
                "INVALID_TOKEN", "This reset token is invalid, expired, or already used.", 422
            )
        return {"message": "Password updated."}, 200


class ChangePasswordResource(Resource):
    method_decorators = [login_required]

    def post(self):
        payload = request.get_json(silent=True) or {}
        try:
            data = ChangePasswordSchema().load(payload)
        except ValidationError as err:
            return _error("VALIDATION_ERROR", str(err.messages), 422)

        if not current_user.check_password(data["current_password"]):
            return _error("WRONG_PASSWORD", "Current password is incorrect.", 401)

        current_user.set_password(data["new_password"])
        current_user.must_reset_password = False
        db.session.commit()
        return {"message": "Password updated."}, 200
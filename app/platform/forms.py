"""
WTForms for the Platform Admin console.
"""

from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Regexp


class PlatformLoginForm(FlaskForm):
    email = StringField(
        "Email Address",
        validators=[DataRequired(message="Email is required."), Email(message="Invalid email address.")],
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(message="Password is required.")],
    )
    submit = SubmitField("Sign In to Platform")


class OnboardOrganizationForm(FlaskForm):
    name = StringField(
        "Organization Name",
        validators=[
            DataRequired(message="Organization name is required."),
            Length(max=150, message="Organization name must be 150 characters or fewer."),
        ],
    )
    subdomain = StringField(
        "Subdomain",
        validators=[
            DataRequired(message="Subdomain is required."),
            Length(min=3, max=63, message="Subdomain must be between 3 and 63 characters."),
            Regexp(
                r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$",
                message="Subdomain must contain only lowercase letters, digits, and hyphens (cannot start or end with a hyphen).",
            ),
        ],
    )
    tenant_admin_name = StringField(
        "Tenant Admin Name",
        validators=[
            DataRequired(message="Tenant Admin name is required."),
            Length(max=120, message="Name must be 120 characters or fewer."),
        ],
    )
    tenant_admin_email = StringField(
        "Tenant Admin Email",
        validators=[
            DataRequired(message="Tenant Admin email is required."),
            Email(message="Invalid email address."),
            Length(max=255, message="Email must be 255 characters or fewer."),
        ],
    )
    submit = SubmitField("Onboard Organization")


class CustomDomainForm(FlaskForm):
    domain_name = StringField(
        "Custom Domain",
        validators=[
            DataRequired(message="Domain name is required."),
            Length(max=255, message="Domain must be 255 characters or fewer."),
            Regexp(
                r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$",
                message="Enter a valid domain name (e.g. treks.example.com).",
            ),
        ],
    )
    submit = SubmitField("Add Custom Domain")


class PlatformProfileForm(FlaskForm):
    name = StringField(
        "Display Name",
        validators=[
            DataRequired(message="Name is required."),
            Length(max=120, message="Name must be 120 characters or fewer."),
        ],
    )
    submit = SubmitField("Save Profile")


class PlatformChangePasswordForm(FlaskForm):
    current_password = PasswordField(
        "Current Password",
        validators=[DataRequired(message="Current password is required.")],
    )
    new_password = PasswordField(
        "New Password",
        validators=[
            DataRequired(message="New password is required."),
            Length(min=8, message="Password must be at least 8 characters long."),
        ],
    )
    confirm_password = PasswordField(
        "Confirm New Password",
        validators=[
            DataRequired(message="Please confirm your new password."),
            EqualTo("new_password", message="Passwords must match."),
        ],
    )
    submit = SubmitField("Change Password")

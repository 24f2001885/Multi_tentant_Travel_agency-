"""
Authentication forms for the TMA Platform (TDR v2 §5.5, §6).
"""

from flask_wtf import FlaskForm
from wtforms import BooleanField, HiddenField, PasswordField, RadioField, SelectField, StringField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional, Regexp

COUNTRY_CODE_CHOICES = [
    ("+91", "+91 (IN)"),
    ("+1", "+1 (US/CA)"),
    ("+44", "+44 (UK)"),
    ("+61", "+61 (AU)"),
    ("+977", "+977 (NP)"),
    ("+975", "+975 (BT)"),
]


class RegisterForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    role = RadioField(
        "Role",
        choices=[("trekker", "Trekker"), ("staff", "Trek Staff")],
        default="trekker",
        validators=[DataRequired()],
    )
    contact_details = StringField("Contact Details", validators=[Optional()])

    # P7 additions
    mobile_country_code = SelectField(
        "Country Code",
        choices=COUNTRY_CODE_CHOICES,
        default="+91",
        validators=[DataRequired()],
    )
    mobile_number = StringField(
        "Mobile Number",
        validators=[
            DataRequired(message="Mobile number is required."),
            Length(min=4, max=20),
            Regexp(r"^[\d\s\-]+$", message="Mobile number can only contain digits, spaces, and hyphens."),
        ],
    )
    accept_terms = BooleanField(
        "I accept the Terms and Conditions and Privacy Policy",
        validators=[DataRequired(message="You must accept the Terms and Conditions and Privacy Policy.")],
    )

    # P11 Communication opt-ins (all default unchecked)
    whatsapp_transactional = BooleanField("Receive booking updates on WhatsApp", default=False)
    sms_transactional = BooleanField("Receive booking updates via SMS", default=False)
    marketing_email = BooleanField("Receive newsletter and trek offers via Email", default=False)
    marketing_whatsapp = BooleanField("Receive trek offers on WhatsApp", default=False)
    marketing_sms = BooleanField("Receive trek offers via SMS", default=False)



class CompleteAccountForm(FlaskForm):
    mobile_country_code = SelectField(
        "Country Code",
        choices=COUNTRY_CODE_CHOICES,
        default="+91",
        validators=[DataRequired()],
    )
    mobile_number = StringField(
        "Mobile Number",
        validators=[
            DataRequired(message="Mobile number is required."),
            Length(min=4, max=20),
            Regexp(r"^[\d\s\-]+$", message="Mobile number can only contain digits, spaces, and hyphens."),
        ],
    )
    accept_terms = BooleanField(
        "I accept the Terms and Conditions and Privacy Policy",
        validators=[DataRequired(message="You must accept the Terms and Conditions and Privacy Policy.")],
    )


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])


class ForgotPasswordForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])


class ResetPasswordForm(FlaskForm):
    token = HiddenField("Token", validators=[DataRequired()])
    new_password = PasswordField("New Password", validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")],
    )


class ForcedPasswordChangeForm(FlaskForm):
    new_password = PasswordField("New Password", validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")],
    )
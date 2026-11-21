from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import BooleanField, FieldList, FormField, IntegerField, PasswordField, RadioField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, EqualTo, Length, NumberRange, Optional


class ParticipantSubform(FlaskForm):
    """Subform for each participant on a group booking."""
    name = StringField("Full Name", validators=[DataRequired(), Length(max=150)])
    age = IntegerField("Age", validators=[Optional(), NumberRange(min=0, max=120)])


class BookingForm(FlaskForm):
    """
    Booking confirmation form supporting group participants and discount coupon code.
    """
    coupon_code = StringField("Coupon Code", validators=[Optional(), Length(max=30)])
    participants = FieldList(FormField(ParticipantSubform), min_entries=0)


class WaitlistForm(FlaskForm):
    """Form to join the FIFO waitlist for a full trek."""
    requested_slot_count = IntegerField(
        "Requested Slots",
        default=1,
        validators=[DataRequired(), NumberRange(min=1, message="Must request at least 1 slot.")],
    )


class CancelBookingForm(FlaskForm):
    """CSRF-protected form for trekker self-cancellation."""
    pass


class ProfileForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    mobile_country_code = StringField("Country Code", default="+91", validators=[Optional(), Length(max=5)])
    mobile_number = StringField("Mobile Number", validators=[Optional(), Length(max=20)])
    submit = SubmitField("Save Profile")


class PasswordChangeForm(FlaskForm):
    current_password = PasswordField("Current Password", validators=[DataRequired()])
    new_password = PasswordField("New Password", validators=[DataRequired(), Length(min=8, message="Password must be at least 8 characters.")])
    confirm_password = PasswordField("Confirm New Password", validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")])
    submit = SubmitField("Change Password")


class DeleteAccountForm(FlaskForm):
    confirm_phrase = StringField("Confirmation", validators=[DataRequired()])
    submit = SubmitField("Permanently Delete Account & Erase Data")


class ProfilePictureForm(FlaskForm):
    picture = FileField(
        "Profile Picture",
        validators=[
            FileRequired(),
            FileAllowed(["jpg", "jpeg"], "JPG images only — please pick a .jpg or .jpeg file."),
        ],
    )


class ReviewForm(FlaskForm):
    rating = RadioField(
        "Rating",
        choices=[('1', '1 Star'), ('2', '2 Stars'), ('3', '3 Stars'), ('4', '4 Stars'), ('5', '5 Stars')],
        validators=[DataRequired(message="Please select a rating from 1 to 5.")],
    )
    comment = TextAreaField("Your Review", validators=[Optional(), Length(max=2000)])
    submit = SubmitField("Submit Review")


class NotificationPreferencesForm(FlaskForm):
    """
    3x3 grid for notification channels and consent preferences.
    Exposes exactly 9 boolean fields.
    """
    email_enabled = BooleanField("Email Enabled", default=True)
    email_transactional = BooleanField("Email Transactional Updates", default=True)
    email_marketing = BooleanField("Email Offers & News", default=False)

    whatsapp_enabled = BooleanField("WhatsApp Enabled", default=True)
    whatsapp_transactional = BooleanField("WhatsApp Transactional Updates", default=False)
    whatsapp_marketing = BooleanField("WhatsApp Offers & News", default=False)

    sms_enabled = BooleanField("SMS Enabled", default=True)
    sms_transactional = BooleanField("SMS Transactional Updates", default=False)
    sms_marketing = BooleanField("SMS Offers & News", default=False)

    submit = SubmitField("Save Preferences")
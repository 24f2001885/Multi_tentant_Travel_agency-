
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import (
    StringField, IntegerField, FloatField, SelectField, SelectMultipleField,
    SubmitField, DateField, TextAreaField, HiddenField
)
from wtforms.validators import DataRequired, Optional, NumberRange, Length


class TrekForm(FlaskForm):
    name = StringField("Trek Name", validators=[DataRequired(), Length(max=100)])
    location = StringField("Location", validators=[DataRequired(), Length(max=100)])
    
    difficulty = SelectField("Difficulty", choices=[
        ('easy', 'Easy'), 
        ('moderate', 'Moderate'), 
        ('hard', 'Hard')
    ], validators=[DataRequired()])
    
    duration_days = IntegerField("Duration (Days)", validators=[DataRequired(), NumberRange(min=1)])
    total_slots = IntegerField("Total Slots", validators=[DataRequired(), NumberRange(min=1)])
    available_slots = IntegerField("Available Slots", validators=[Optional(), NumberRange(min=0)])
    price = FloatField("Price (₹)", validators=[DataRequired(), NumberRange(min=0)])
    
    start_date = DateField("Start Date", format='%Y-%m-%d', validators=[Optional()])
    end_date = DateField("End Date", format='%Y-%m-%d', validators=[Optional()])
    
    status = SelectField("Status", choices=[
        ('pending', 'Pending'), 
        ('approved', 'Approved'), 
        ('open', 'Open'), 
        ('closed', 'Closed'), 
        ('completed', 'Completed')
    ], validators=[DataRequired()])
    
    assigned_staff_id = SelectField("Assigned Staff", coerce=int, validators=[Optional()])
    
    # New Database Fields for Media and Text
    description = TextAreaField("About this Trek", validators=[Optional()])

    itinerary_layout = SelectField(
        "Itinerary Display Style",
        choices=[
            ("accordion", "Interactive Accordion (Compact Collapsible)"),
            ("timeline", "Mountain Trail Timeline (Step-by-Step Path)"),
            ("magazine", "Expedition Magazine / PDF Brochure (Rich Multi-Column)"),
        ],
        default="accordion",
        validators=[DataRequired()],
    )
    
    thumbnail = FileField("Trek Thumbnail", validators=[
        FileAllowed(['jpg', 'jpeg', 'png'], 'Images only! (JPG, PNG)')
    ])
    
    submit = SubmitField("Save Trek")


class StaffActionForm(FlaskForm):
    action = HiddenField("Action", validators=[DataRequired()])
    submit = SubmitField("Confirm")


class UserSearchForm(FlaskForm):
    search = StringField("Search Users", validators=[Optional()])
    
    role = SelectField("Role", choices=[
        ('', 'Any Role'), 
        ('admin', 'Admin'), 
        ('staff', 'Staff'), 
        ('trekker', 'Trekker')
    ], validators=[Optional()])
    
    is_active = SelectField("Status", choices=[
        ('', 'Any Status'), 
        ('true', 'Active'), 
        ('false', 'Inactive')
    ], validators=[Optional()])
    
    submit = SubmitField("Search")


class ItineraryDayForm(FlaskForm):
    title = StringField("Day Title", validators=[DataRequired(), Length(max=150)])
    description = TextAreaField("Description", validators=[Optional()])
    submit = SubmitField("Save Day")


class ItineraryElementForm(FlaskForm):
    element_type = SelectField(
        "Element Type",
        choices=[
            ('activity', 'Activity'),
            ('meal', 'Meal'),
            ('transport', 'Transport'),
            ('accommodation', 'Accommodation'),
            ('note', 'Note'),
        ],
        validators=[DataRequired()],
    )
    title = StringField("Title", validators=[DataRequired(), Length(max=150)])
    description = TextAreaField("Description", validators=[Optional()])
    time_of_day = StringField("Time of Day", validators=[Optional(), Length(max=20)])
    submit = SubmitField("Save Item")


class MediaUploadForm(FlaskForm):
    file = FileField(
        "Upload Image",
        validators=[
            DataRequired(),
            FileAllowed(['jpg', 'jpeg', 'png', 'webp'], "Images only (JPG, PNG, WEBP)!"),
        ],
    )
    caption = StringField("Caption", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Upload")


class MediaCaptionForm(FlaskForm):
    caption = StringField("Caption", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Update Caption")


class CampaignForm(FlaskForm):
    channel = SelectField(
        "Channel",
        choices=[("email", "Email"), ("whatsapp", "WhatsApp"), ("sms", "SMS")],
        validators=[Optional()],
    )
    channels = SelectMultipleField(
        "Channels",
        choices=[("email", "Email"), ("whatsapp", "WhatsApp"), ("sms", "SMS")],
        validators=[Optional()],
    )
    subject = StringField("Subject", validators=[Optional(), Length(max=200)])
    template_body = TextAreaField("Message Body", validators=[DataRequired()])
    poster = FileField(
        "Upload Poster Image",
        validators=[Optional(), FileAllowed(["jpg", "jpeg", "png", "webp"], "Images only (.jpg, .png, .webp)")],
    )
    poster_url = StringField("Or Poster Image URL", validators=[Optional(), Length(max=500)])
    provider_template_name = StringField(
        "WhatsApp Template Name",
        validators=[Optional(), Length(max=100)],
    )

    # Audience filter builder fields
    roles = SelectField(
        "Target Role",
        choices=[("", "All roles"), ("trekker", "Trekkers only"), ("staff", "Staff only")],
        validators=[Optional()],
    )
    booking_statuses = SelectField(
        "Booking Status",
        choices=[
            ("", "Any booking status"),
            ("booked", "Booked"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        validators=[Optional()],
    )
    signup_from = DateField("Signed Up After", format="%Y-%m-%d", validators=[Optional()])
    signup_to = DateField("Signed Up Before", format="%Y-%m-%d", validators=[Optional()])
    is_active = SelectField(
        "Account Status",
        choices=[("", "Any"), ("true", "Active users only"), ("false", "Inactive users")],
        validators=[Optional()],
    )
    has_any_booking = SelectField(
        "Booking History",
        choices=[("", "Any"), ("true", "Has at least one booking"), ("false", "No bookings")],
        validators=[Optional()],
    )
    submit = SubmitField("Save Campaign")

    def validate(self, extra_validators=None):
        initial_valid = super().validate(extra_validators=extra_validators)
        if not initial_valid:
            return False

        if self.channel.data == "email" and not (self.subject.data or "").strip():
            self.subject.errors.append("Subject is required for email campaigns.")
            return False

        return True


class TestSendForm(FlaskForm):
    test_email = StringField("Test Email Address", validators=[Optional(), Length(max=255)])
    test_mobile = StringField("Test Mobile Number (e.g. +919876543210)", validators=[Optional(), Length(max=30)])
    submit = SubmitField("Send Test Message")


class StaffCreateForm(FlaskForm):
    name = StringField("Full Name", validators=[DataRequired(), Length(max=120)])
    email = StringField("Email Address", validators=[DataRequired(), Length(max=255)])
    mobile_country_code = SelectField("Country Code", choices=[("+91", "+91 (India)"), ("+1", "+1 (USA/Canada)"), ("+44", "+44 (UK)"), ("+61", "+61 (Australia)"), ("+977", "+977 (Nepal)")], default="+91", validators=[DataRequired()])
    mobile_number = StringField("Mobile Number", validators=[DataRequired(), Length(min=4, max=20)])
    role_id = SelectField("Assigned Role", validators=[DataRequired()])
    contact_details = TextAreaField("Qualifications / Notes", validators=[Optional()])
    submit = SubmitField("Add Staff Member")
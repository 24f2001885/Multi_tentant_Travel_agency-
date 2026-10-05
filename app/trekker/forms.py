from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import StringField
from wtforms.validators import DataRequired


class BookingForm(FlaskForm):
    """Bare CSRF-protected confirm form — the trek_id is already in the URL."""

    pass


class ProfileForm(FlaskForm):
    """
    Only `name` is actually editable here. The mockups show a "Contact"
    field too, but the users table has no column for it, so it's left out
    rather than bolted on; email stays read-only.
    """

    name = StringField("Name", validators=[DataRequired()])


class ProfilePictureForm(FlaskForm):
    """Single-purpose form for uploading a profile picture — restricted to
    .jpg/.jpeg only."""
    
    picture = FileField(
        "Profile Picture",
        validators=[
            FileRequired(),
            FileAllowed(["jpg", "jpeg"], "JPG images only — please pick a .jpg or .jpeg file.")
        ]
    )
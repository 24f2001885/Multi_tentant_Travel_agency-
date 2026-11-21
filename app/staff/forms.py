
from flask_wtf import FlaskForm
from wtforms import IntegerField
from wtforms.validators import DataRequired, NumberRange


class TrekSlotStatusForm(FlaskForm):
    

    available_slots = IntegerField(
        "Available Slots",
        validators=[
            DataRequired(),
            NumberRange(min=0, message="Available slots cannot be negative."),
        ],
    )


from wtforms import StringField, SelectField, FloatField, DateField, TextAreaField, SubmitField
from wtforms.validators import Length, Optional, ValidationError


class StaffTrekCreateForm(FlaskForm):
    name = StringField("Trek Name", validators=[Optional(), Length(max=150)])
    title = StringField("Trek Title", validators=[Optional(), Length(max=150)])
    description = TextAreaField("Description", validators=[Optional()])
    location = StringField("Location", validators=[DataRequired(), Length(max=150)])
    difficulty = SelectField(
        "Difficulty",
        choices=[('easy', 'Easy'), ('moderate', 'Moderate'), ('hard', 'Hard')],
        validators=[DataRequired()],
    )
    duration_days = IntegerField("Duration (Days)", validators=[DataRequired(), NumberRange(min=1)])
    total_slots = IntegerField("Total Slots", validators=[DataRequired(), NumberRange(min=1)])
    price = FloatField("Price per Slot (₹)", validators=[DataRequired(), NumberRange(min=0.01)])
    itinerary_layout = SelectField(
        "Itinerary Display Style",
        choices=[
            ("accordion", "Interactive Accordion (Compact Collapsible)"),
            ("timeline", "Mountain Trail Timeline (Step-by-Step Path)"),
            ("magazine", "Expedition Magazine / PDF Brochure (Rich Multi-Column)"),
        ],
        default="accordion",
        validators=[Optional()],
    )
    start_date = DateField("Start Date", format='%Y-%m-%d', validators=[DataRequired()])
    end_date = DateField("End Date", format='%Y-%m-%d', validators=[DataRequired()])
    submit = SubmitField("Create Trek (Submit for Review)")

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators=extra_validators):
            return False
        if not self.name.data and not self.title.data:
            self.name.errors.append("Trek name is required.")
            return False
        if not self.name.data and self.title.data:
            self.name.data = self.title.data
        return True

    def validate_end_date(self, field):
        if self.start_date.data and field.data and field.data < self.start_date.data:
            raise ValidationError("End date cannot be earlier than start date.")
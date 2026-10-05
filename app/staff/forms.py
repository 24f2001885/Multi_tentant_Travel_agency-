
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
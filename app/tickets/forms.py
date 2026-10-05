

from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

class RaiseTicketForm(FlaskForm):
   
    type = SelectField(
        'Ticket Type',
        choices=[('query', 'Query'), ('problem', 'Problem')],
        validators=[DataRequired(message="Please select a ticket type.")]
    )
    
    subject = StringField(
        'Subject',
        validators=[
            DataRequired(message="Subject is required."), 
            Length(max=150, message="Subject cannot exceed 150 characters.")
        ]
    )
    
    message = TextAreaField(
        'Message',
        validators=[DataRequired(message="Message cannot be empty.")]
    )
    
    booking_id = SelectField(
        'Associated Booking (Optional)',
        coerce=int,
        validators=[Optional()]
    )
  
class RespondTicketForm(FlaskForm):
    """
    Form for Admins and Guides to respond to a ticket.
    """
    response = TextAreaField(
        'Response',
        validators=[DataRequired(message="Response cannot be blank.")]
    )
   
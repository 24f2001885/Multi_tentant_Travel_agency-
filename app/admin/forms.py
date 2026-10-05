
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import (
    StringField, IntegerField, FloatField, SelectField, 
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
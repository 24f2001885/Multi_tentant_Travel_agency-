 
from datetime import datetime, timezone
from app.extensions import db

class Trek(db.Model):
    __tablename__ = 'treks'
 
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    location = db.Column(db.String(150), nullable=False)
    difficulty = db.Column(db.String(20), nullable=False)
    duration_days = db.Column(db.Integer, nullable=False)
    total_slots = db.Column(db.Integer, nullable=False)
    available_slots = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    assigned_staff_id = db.Column(db.Integer, db.ForeignKey('staff_profiles.id'), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='pending')
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    thumbnail_filename = db.Column(db.String(255), nullable=True)
 
    __table_args__ = (
        db.CheckConstraint(
            difficulty.in_(['easy', 'moderate', 'hard']),
            name='check_valid_difficulty'
        ),
        db.CheckConstraint(
            duration_days > 0,
            name='check_positive_duration'
        ),
        db.CheckConstraint(
            total_slots >= 0,
            name='check_positive_total_slots'
        ),
        db.CheckConstraint(
            'available_slots >= 0 AND available_slots <= total_slots',
            name='check_valid_available_slots'
        ),
        db.CheckConstraint(
            status.in_(['pending', 'approved', 'open', 'closed', 'completed']),
            name='check_valid_status'
        ),
    )
 
    # One-to-many to Booking
    bookings = db.relationship('Booking', back_populates='trek', lazy=True, cascade="all, delete-orphan")
     
    assigned_staff = db.relationship('StaffProfile', back_populates='treks', lazy=True)

    def __repr__(self):
        return f"<Trek {self.id} '{self.name}' status={self.status}>"
 
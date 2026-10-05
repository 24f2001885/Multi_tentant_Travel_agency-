

from datetime import datetime, timezone
from sqlalchemy import text
from app.extensions import db

class Booking(db.Model):
    __tablename__ = 'bookings'

    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    trek_id = db.Column(db.Integer, db.ForeignKey('treks.id'), nullable=False)
    booking_date = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    status = db.Column(db.String(20), nullable=False, default='pending')
    payment_status = db.Column(db.String(20), nullable=False, default='pending')

    
    __table_args__ = (
        db.CheckConstraint(
            status.in_(['pending', 'booked', 'cancelled', 'completed']),
            name='check_valid_booking_status'
        ),
        db.CheckConstraint(
            payment_status.in_(['pending', 'paid', 'failed', 'refunded']),
            name='check_valid_payment_status'
        ),
        # 2. DB-level duplicate-booking guard (partial unique index)
        db.Index(
            'ix_unique_active_booking',
            'user_id',
            'trek_id',
            unique=True,
            sqlite_where=text("status IN ('pending','booked')")
        ),
    )

    
    # Many-to-one to User
    user = db.relationship('User', back_populates='bookings', lazy=True)
    
    # Many-to-one to Trek
    trek = db.relationship('Trek', back_populates='bookings', lazy=True)
    
    # One-to-many to Payment
    payments = db.relationship('Payment', back_populates='booking', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Booking {self.id} user_id={self.user_id} trek_id={self.trek_id} status={self.status}>"



from datetime import datetime, timezone
from app.extensions import db

class Payment(db.Model):
    __tablename__ = 'payments'

    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default='INR')
    gateway_order_id = db.Column(db.String(100), nullable=False)  # Razorpay order_id
    transaction_id = db.Column(db.String(100), unique=True, nullable=True)  # Razorpay payment_id
    status = db.Column(db.String(20), nullable=False, default='initiated')
    payment_date = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    
    __table_args__ = (
        db.CheckConstraint(
            status.in_(['initiated', 'success', 'failed', 'refunded']),
            name='check_valid_payment_status'
        ),
    )

    # 2. Relationship: many-to-one to Booking
    booking = db.relationship('Booking', back_populates='payments', lazy=True)

    def __repr__(self):
        return f"<Payment {self.id} booking_id={self.booking_id} status={self.status} transaction_id={self.transaction_id}>"

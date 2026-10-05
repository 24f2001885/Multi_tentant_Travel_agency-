 
from datetime import datetime, timezone, timedelta
from app.extensions import db

class Ticket(db.Model):
    __tablename__ = 'tickets'
 
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    trek_id = db.Column(db.Integer, db.ForeignKey('treks.id'), nullable=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=True)
    type = db.Column(db.String(10), nullable=False)
    subject = db.Column(db.String(150), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='open')
    response = db.Column(db.Text, nullable=True)
    responded_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    responded_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    purge_after = db.Column(db.DateTime, nullable=False)
 
    __table_args__ = (
        db.CheckConstraint(
            type.in_(['query', 'problem']),
            name='check_valid_ticket_type'
        ),
        db.CheckConstraint(
            status.in_(['open', 'answered', 'resolved']),
            name='check_valid_ticket_status'
        ),
    )
  
    user = db.relationship('User', foreign_keys=[user_id], back_populates='tickets', lazy=True)
    trek = db.relationship('Trek', foreign_keys=[trek_id], lazy=True)
    booking = db.relationship('Booking', foreign_keys=[booking_id], lazy=True)
    responded_by = db.relationship('User', foreign_keys=[responded_by_id], lazy=True)
 
    def __init__(self, **kwargs):
        super().__init__(**kwargs) 
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc)
            
        if not self.purge_after and self.type:
             
            query_days = 10
            problem_days = 90
            
            try:
                from flask import current_app
                if current_app:
                    query_days = current_app.config.get('QUERY_TICKET_RETENTION_DAYS', 10)
                    problem_days = current_app.config.get('PROBLEM_TICKET_RETENTION_DAYS', 90)
            except RuntimeError:
                pass  # Use defaults if outside app context
            
            if self.type == 'query':
                self.purge_after = self.created_at + timedelta(days=query_days)
            elif self.type == 'problem':
                self.purge_after = self.created_at + timedelta(days=problem_days)

    def __repr__(self):
        return f"<Ticket {self.id} type={self.type} status={self.status} purge_after={self.purge_after}>"
 
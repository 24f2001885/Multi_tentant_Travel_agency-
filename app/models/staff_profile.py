 
from app.extensions import db

class StaffProfile(db.Model):
    __tablename__ = 'staff_profiles'
 
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    contact_details = db.Column(db.String(255), nullable=True)
    approval_status = db.Column(db.String(20), nullable=False, default='pending')
    approved_by_admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
 
    __table_args__ = (
        db.CheckConstraint(
            approval_status.in_(['pending', 'approved', 'blacklisted']),
            name='check_valid_approval_status'
        ),
    )
 
    user = db.relationship(
        'User', 
        foreign_keys=[user_id], 
        back_populates='staff_profile'
    )
 
    approved_by_admin = db.relationship(
        'User', 
        foreign_keys=[approved_by_admin_id]
    )
 
    treks = db.relationship(
        'Trek', 
        back_populates='assigned_staff', 
        lazy=True
    )

    def __repr__(self):
        return f"<StaffProfile {self.id} user_id={self.user_id} approval_status={self.approval_status}>"
 
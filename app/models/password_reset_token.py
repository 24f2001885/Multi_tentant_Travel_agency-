

from datetime import datetime, timezone
from app.extensions import db

class PasswordResetToken(db.Model):
    __tablename__ = 'password_reset_tokens'

    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token_hash = db.Column(db.String(255), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    
    user = db.relationship('User', lazy=True)

    
    def is_valid(self) -> bool:
       
        now = datetime.now(timezone.utc)
        expires = self.expires_at
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
            
        return self.used_at is None and expires > now

    def __repr__(self):
        return f"<PasswordResetToken {self.id} user_id={self.user_id}>"

    
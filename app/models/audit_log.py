
from datetime import datetime, timezone
from app.extensions import db

class AuditLog(db.Model):
    __tablename__ = 'audit_logs'

    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    action = db.Column(db.String(100), nullable=False)
    target_type = db.Column(db.String(50), nullable=False)
    target_id = db.Column(db.Integer, nullable=False)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    
    actor = db.relationship('User', lazy=True)

    # 2. Helper classmethod to build and return an AuditLog instance
    @classmethod
    def record(cls, actor_id: int, action: str, target_type: str, target_id: int, details: str = None) -> 'AuditLog':
        """
        Builds and returns an AuditLog instance for the specified action.
        Does NOT call db.session.add() or db.session.commit() — leaves 
        transaction control strictly to the caller.
        """
        return cls(
            actor_user_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details
        )

    def __repr__(self):
        return f"<AuditLog {self.id} actor_user_id={self.actor_user_id} action='{self.action}' target={self.target_type}:{self.target_id}>"

    
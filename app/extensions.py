from flask_apscheduler import APScheduler
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_mail import Mail
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from werkzeug.routing import BaseConverter, ValidationError

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
mail = Mail()
csrf = CSRFProtect()
scheduler = APScheduler()
limiter = Limiter(key_func=get_remote_address)
migrate = Migrate()


class HashidConverter(BaseConverter):
    """
    Werkzeug URL converter for reversible opaque Hashids.
    Decodes incoming path tokens to integer IDs and encodes integer IDs to tokens on url_for().
    """

    def to_python(self, value: str) -> int:
        from app.services.id_codec import decode

        decoded = decode(value)
        if decoded is None:
            raise ValidationError()
        # print(f"[DEBUG] HashidConverter to_python value={value}")
        return decoded

    def to_url(self, value) -> str:
        from app.services.id_codec import encode

        if isinstance(value, int):
            return encode(value)
        try:
            return encode(int(value))
        except (ValueError, TypeError):
            return str(value)
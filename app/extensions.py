
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail
from flask_wtf.csrf import CSRFProtect
from flask_apscheduler import APScheduler
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate  # <-- Added Import


db = SQLAlchemy()


login_manager = LoginManager()
login_manager.login_view = "auth.login"


mail = Mail()

csrf = CSRFProtect()


scheduler = APScheduler()


limiter = Limiter(key_func=get_remote_address)

migrate = Migrate()  
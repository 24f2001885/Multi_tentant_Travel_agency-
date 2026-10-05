
import os
import click
from datetime import timedelta
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, abort
from werkzeug.security import generate_password_hash
from flask_login import login_required, current_user

from app.extensions import csrf, db, limiter, login_manager, mail, migrate, scheduler
from app.models import Trek
from app.services import history_service, trek_service, ticket_service
from app.auth.decorators import role_required


def create_app(config_object=None) -> Flask:
    # Load environment variables from .env if present
    load_dotenv()

    app = Flask(__name__)

    # 1.env imp
    secret_key = os.environ.get("SECRET_KEY")
    database_url = os.environ.get("DATABASE_URL")

    if not secret_key:
        raise ValueError(
            "CRITICAL ERROR: SECRET_KEY environment variable is missing. "
            "Cannot start application securely."
        )
    if not database_url:
        raise ValueError(
            "CRITICAL ERROR: DATABASE_URL environment variable is missing. "
            "Cannot connect to the database."
        )

    #    configuration
    app.config["SECRET_KEY"] = secret_key
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:////app/tma.db"
    os.makedirs("/app/instance", exist_ok=True)

    #env load
    app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER")
    app.config["MAIL_PORT"] = (
        int(os.environ.get("MAIL_PORT", 25)) if os.environ.get("MAIL_PORT") else None
    )
    app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "False").lower() in (
        "true", "1", "yes", "t"
    )
    app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME")
    app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD")
    app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER")

    app.config["RAZORPAY_KEY_ID"] = os.environ.get("RAZORPAY_KEY_ID")
    app.config["RAZORPAY_KEY_SECRET"] = os.environ.get("RAZORPAY_KEY_SECRET")
    app.config["RAZORPAY_WEBHOOK_SECRET"] = os.environ.get("RAZORPAY_WEBHOOK_SECRET")

    app.config["ADMIN_EMAIL"] = os.environ.get("ADMIN_EMAIL")
    app.config["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD")

    # Numeric configurations with safe casting
    if os.environ.get("RESET_TOKEN_EXPIRY_MINUTES"):
        app.config["RESET_TOKEN_EXPIRY_MINUTES"] = int(
            os.environ.get("RESET_TOKEN_EXPIRY_MINUTES")
        )
    if os.environ.get("QUERY_TICKET_RETENTION_DAYS"):
        app.config["QUERY_TICKET_RETENTION_DAYS"] = int(
            os.environ.get("QUERY_TICKET_RETENTION_DAYS")
        )
    if os.environ.get("PROBLEM_TICKET_RETENTION_DAYS"):
        app.config["PROBLEM_TICKET_RETENTION_DAYS"] = int(
            os.environ.get("PROBLEM_TICKET_RETENTION_DAYS")
        )

    #    external config object
    if config_object:
        app.config.from_object(config_object)

    # session cookie / security hardening
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = not app.debug
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)

    razorpay_key = app.config.get("RAZORPAY_KEY_ID") or ""
    if not app.config["SESSION_COOKIE_SECURE"] and not razorpay_key.startswith("rzp_test_"):
        app.logger.warning(
            "SESSION_COOKIE_SECURE is False while RAZORPAY_KEY_ID does not look like a test "
            "key — verify this configuration before deploying beyond a local demo."
        )
    
    #profile pic 
    app.config["PROFILE_PIC_UPLOAD_FOLDER"] = os.path.join(
        app.root_path, "static", "uploads", "profile_pics"
    )
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB upload cap
    os.makedirs(app.config["PROFILE_PIC_UPLOAD_FOLDER"], exist_ok=True)
    
    # 3. Initialize 
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    mail.init_app(app)
    csrf.init_app(app)
    scheduler.init_app(app)
    limiter.init_app(app)

    login_manager.login_view = "auth.login"
    
    # Flask-Login  loader user
    @login_manager.user_loader
    def load_user(user_id):
        from app.models.user import User
        return User.query.get(int(user_id))

     #flask seed-admin
    @app.cli.command("seed-admin")
    def seed_admin():
        """Seeds the database with the initial Admin user if one does not already exist."""
        from app.models.user import User

        admin_email = app.config.get("ADMIN_EMAIL")
        admin_password = app.config.get("ADMIN_PASSWORD")

        if not admin_email or not admin_password:
            click.echo("Error: ADMIN_EMAIL or ADMIN_PASSWORD not found in environment variables.")
            return

        existing_admin = User.query.filter_by(role="admin").first()
        if existing_admin:
            click.echo("An Admin user already exists. Skipping duplicate creation.")
            return

        hashed_password = generate_password_hash(admin_password)

        new_admin = User(
            name="System Admin",
            email=admin_email,
            password_hash=hashed_password,
            role="admin",
            is_active=True,
        )

        db.session.add(new_admin)
        db.session.commit()
        click.echo(f"Successfully created Admin user: {admin_email}")

    from app.auth import auth_bp
    app.register_blueprint(auth_bp)

    from app.admin import admin_bp
    app.register_blueprint(admin_bp)

    from app.staff import staff_bp
    app.register_blueprint(staff_bp)

    from app.trekker import trekker_bp
    app.register_blueprint(trekker_bp)

    from app.api import api_bp
    app.register_blueprint(api_bp)

    from app.tickets import tickets_bp
    app.register_blueprint(tickets_bp)

    
    from app.main import main_bp
    app.register_blueprint(main_bp) 

    # Error handlers
    @app.errorhandler(403)
    def _forbidden(error):
        return render_template("errors/generic.html", code=403), 403

    @app.errorhandler(404)
    def _not_found(error):
        return render_template("errors/generic.html", code=404), 404

    @app.errorhandler(413)
    def _too_large(error):
        """Catches RequestEntityTooLarge when an upload exceeds MAX_CONTENT_LENGTH."""
        return render_template("errors/generic.html", code=413), 413

    @app.errorhandler(500)
    def _server_error(error):
        return render_template("errors/generic.html", code=500), 500

    
    if not app.config.get("SCHEDULER_STARTED"):
        from app.services import payment_service
        if scheduler.get_job("expire_stale_bookings") is None:
            scheduler.add_job(
                id="expire_stale_bookings",
                func=payment_service.expire_stale_bookings,
                trigger="interval",
                minutes=15,
            )
        
        
        if scheduler.get_job("purge_expired_tickets") is None:
            scheduler.add_job(
                id="purge_expired_tickets",
                func=ticket_service.purge_expired,
                trigger="cron",
                hour=2,
                minute=0,
            )
        
        if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
            scheduler.start()
        app.config["SCHEDULER_STARTED"] = True

    
    return app
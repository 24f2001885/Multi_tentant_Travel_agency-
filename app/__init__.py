import os
import time
import click
from datetime import timedelta
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, abort, g, session
from werkzeug.security import generate_password_hash
from flask_login import login_required, current_user
from sqlalchemy import select

from app.extensions import csrf, db, limiter, login_manager, mail, migrate, scheduler, HashidConverter
from app.models import Trek
from app.services import history_service, trek_service, ticket_service, id_codec
from app.auth.decorators import role_required
from app.tenancy.resolver import init_tenancy


def create_app(config_object=None) -> Flask:
    # Load environment variables from .env if present
    load_dotenv()

    app = Flask(__name__)

    # 1. Environment & Startup Checks (Fail fast)
    secret_key = os.environ.get("SECRET_KEY")
    database_url = os.environ.get("DATABASE_URL")
    hashids_salt = os.environ.get("HASHIDS_SALT")

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
    if not hashids_salt:
        raise ValueError(
            "CRITICAL ERROR: HASHIDS_SALT environment variable is missing. "
            "Cannot start application securely."
        )

    if not database_url.startswith("postgresql"):
        app.logger.warning(
            "DATABASE_URL is not postgresql (concurrency/isolation suites require Postgres)."
        )

    # 2. Configuration
    app.config["SECRET_KEY"] = secret_key

    # Normalize postgres URL to postgresql+psycopg2 driver
    db_uri = database_url
    if db_uri.startswith("postgres://"):
        db_uri = db_uri.replace("postgres://", "postgresql+psycopg2://", 1)
    elif db_uri.startswith("postgresql://") and not db_uri.startswith("postgresql+"):
        db_uri = db_uri.replace("postgresql://", "postgresql+psycopg2://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
    os.makedirs(app.instance_path, exist_ok=True)

    # Email configuration
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

    # Razorpay payment keys
    app.config["RAZORPAY_KEY_ID"] = os.environ.get("RAZORPAY_KEY_ID")
    app.config["RAZORPAY_KEY_SECRET"] = os.environ.get("RAZORPAY_KEY_SECRET")
    app.config["RAZORPAY_WEBHOOK_SECRET"] = os.environ.get("RAZORPAY_WEBHOOK_SECRET")

    # WhatsApp Cloud API & Twilio configuration
    app.config["WHATSAPP_PROVIDER"] = os.environ.get("WHATSAPP_PROVIDER", "meta_cloud_api")
    app.config["WHATSAPP_ACCESS_TOKEN"] = os.environ.get("WHATSAPP_ACCESS_TOKEN")
    app.config["WHATSAPP_PHONE_NUMBER_ID"] = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    app.config["WHATSAPP_BUSINESS_ACCOUNT_ID"] = os.environ.get("WHATSAPP_BUSINESS_ACCOUNT_ID")
    app.config["WHATSAPP_WEBHOOK_VERIFY_TOKEN"] = os.environ.get("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "tma_trek_token_123")
    app.config["WHATSAPP_APP_SECRET"] = os.environ.get("WHATSAPP_APP_SECRET")
    app.config["WHATSAPP_TEMPLATE_LANG"] = os.environ.get("WHATSAPP_TEMPLATE_LANG", "en_US")

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

    # External config object or dict override if provided (e.g. test configs)
    if config_object:
        if isinstance(config_object, dict):
            app.config.from_mapping(config_object)
        else:
            app.config.from_object(config_object)

    # Session cookie / security hardening
    # Note: Never set SESSION_COOKIE_DOMAIN — host-only cookies keep tenant sessions separate.
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

    # Uploads
    app.config["PROFILE_PIC_UPLOAD_FOLDER"] = os.path.join(
        app.root_path, "static", "uploads", "profile_pics"
    )
    app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024  # 30 MB upload cap (allows high-res trek photos and banners)
    os.makedirs(app.config["PROFILE_PIC_UPLOAD_FOLDER"], exist_ok=True)

    # Comms provider validation (Milestone P11)
    email_prov = app.config.get("EMAIL_PROVIDER", os.environ.get("EMAIL_PROVIDER", "smtp")).lower()
    sms_prov = app.config.get("SMS_PROVIDER", os.environ.get("SMS_PROVIDER", "twilio")).lower()
    wa_prov = app.config.get("WHATSAPP_PROVIDER", os.environ.get("WHATSAPP_PROVIDER", "meta_cloud_api")).lower()
    app.config["SENDGRID_API_KEY"] = app.config.get("SENDGRID_API_KEY", os.environ.get("SENDGRID_API_KEY"))

    valid_email_provs = ("smtp", "sendgrid", "ses")
    if email_prov not in valid_email_provs:
        raise ValueError(f"Unknown EMAIL_PROVIDER '{email_prov}'. Must be one of {valid_email_provs}.")

    valid_sms_provs = ("twilio",)
    if sms_prov not in valid_sms_provs:
        raise ValueError(f"Unknown SMS_PROVIDER '{sms_prov}'. Must be one of {valid_sms_provs}.")

    valid_wa_provs = ("meta_cloud_api", "twilio")
    if wa_prov not in valid_wa_provs:
        raise ValueError(f"Unknown WHATSAPP_PROVIDER '{wa_prov}'. Must be one of {valid_wa_provs}.")

    if not app.debug and not app.config.get("TESTING"):
        if email_prov == "sendgrid" and not app.config.get("SENDGRID_API_KEY"):
            raise ValueError("SENDGRID_API_KEY is required when EMAIL_PROVIDER is 'sendgrid'.")

    # 3. Extensions initialization
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    mail.init_app(app)
    csrf.init_app(app)
    try:
        scheduler.init_app(app)
    except Exception:
        pass
    limiter.init_app(app)

    # 4. URL Map Converters (Register BEFORE any blueprints)
    app.url_map.converters["hashid"] = HashidConverter

    # 5. Initialize Tenancy Subsystem (Host resolution & request hooks)
    init_tenancy(app)

    # 5.1 Real-time Request Telemetry (Latency & HTTP Status tracking)
    @app.before_request
    def _record_request_start():
        g.request_start_time = time.time()

    @app.after_request
    def _record_request_finish(response):
        start_time = getattr(g, "request_start_time", None)
        if start_time is not None:
            duration_ms = (time.time() - start_time) * 1000.0
            from app.services.monitoring_service import telemetry
            telemetry.record(
                method=request.method,
                host=request.host,
                path=request.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        return response

    login_manager.login_view = "auth.login"

    # Flask-Login user loader (scoped to active tenant)
    @login_manager.user_loader
    def load_user(user_id):
        from app.models.user import User
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            return None
        org_id = getattr(g, "organization_id", None)
        if not org_id:
            return None
        return db.session.execute(
            select(User).where(User.id == uid, User.organization_id == org_id)
        ).scalar_one_or_none()

    # 6. Context processors & Jinja filters
    @app.context_processor
    def inject_tenancy():
        org = getattr(g, "organization", None)
        branding = org.branding() if org and hasattr(org, "branding") else {}
        return {"current_org": branding}

    @app.template_filter("hid")
    def hid_filter(val):
        if val is None:
            return ""
        return id_codec.encode(val)

    @app.template_global("has_capability")
    def has_capability(key: str) -> bool:
        from flask_login import current_user
        from app.services.rbac_service import PROTECTED_CAPABILITY_KEYS
        if not current_user or not getattr(current_user, "is_authenticated", False):
            return False
        org = getattr(g, "organization", None)
        if org and current_user.id == org.tenant_admin_user_id:
            return key not in PROTECTED_CAPABILITY_KEYS
        caps = session.get("capability_keys") or []
        return key in caps

    @app.template_global("tenant_has_feature")
    def tenant_has_feature(key: str) -> bool:
        org = getattr(g, "organization", None)
        if not org or not hasattr(org, "has_feature"):
            return True
        return org.has_feature(key)

    # 7. Blueprints Registration (public_bp MUST be registered LAST)
    from app.platform import platform_bp, platform_root_bp
    app.register_blueprint(platform_bp)
    app.register_blueprint(platform_root_bp)

    from app.auth import auth_bp
    app.register_blueprint(auth_bp)

    from app.admin import admin_bp
    app.register_blueprint(admin_bp)

    from app.staff import staff_bp
    app.register_blueprint(staff_bp)

    from app.trekker import trekker_bp, trekker_root_bp
    app.register_blueprint(trekker_bp)
    app.register_blueprint(trekker_root_bp)

    from app.api import api_bp
    app.register_blueprint(api_bp)

    # Route /webhook alias for WhatsApp webhook (compatible with root /webhook and /api/whatsapp/webhook)
    from app.api.whatsapp_webhook_resource import WhatsAppWebhookResource
    webhook_view = WhatsAppWebhookResource.as_view("whatsapp_webhook_root")
    app.add_url_rule("/webhook", view_func=webhook_view, methods=["GET", "POST"])
    csrf.exempt(webhook_view)

    from app.tickets import tickets_bp
    app.register_blueprint(tickets_bp)

    from app.public import public_bp
    app.register_blueprint(public_bp)

    # 8. Dynamic Tenant Theme Loader
    from app.tenancy.theme_loader import setup_tenant_theme_loader
    setup_tenant_theme_loader(app)

    # # print(f"[DEBUG] create_app v2: db={app.config.get('DATABASE_URL','')[:12]} converters={list(app.url_map.converters)}")

    # 8. CLI Commands
    @app.cli.command("seed-platform-admin")
    def seed_platform_admin():
        """Idempotently seeds the Platform Admin account from environment variables."""
        from app.models.platform_admin import PlatformAdmin
        from app.tenancy.scoping import tenant_bypass

        name = os.environ.get("PLATFORM_ADMIN_NAME")
        email = os.environ.get("PLATFORM_ADMIN_EMAIL")
        password = os.environ.get("PLATFORM_ADMIN_PASSWORD")

        if not name or not email or not password:
            raise click.ClickException(
                "PLATFORM_ADMIN_NAME, PLATFORM_ADMIN_EMAIL, and PLATFORM_ADMIN_PASSWORD must all be set."
            )

        clean_email = email.lower().strip()
        with tenant_bypass():
            existing = PlatformAdmin.query.filter_by(email=clean_email).first()
            if existing:
                existing.name = name.strip()
                existing.set_password(password)
                existing.is_active = True
                db.session.commit()
                click.echo(f"Updated Platform Admin '{clean_email}' password from environment (id={existing.id}).")
                return

            admin = PlatformAdmin(
                name=name.strip(),
                email=clean_email,
                is_active=True,
            )
            admin.set_password(password)
            db.session.add(admin)
            db.session.commit()
            click.echo(f"Seeded Platform Admin: {admin.email} (id={admin.id})")

    @app.cli.command("init-live-tenants")
    def init_live_tenants_cmd():
        """Idempotently activates Tenant Zero and Apex Altitude Expeditions and suspends all others."""
        from app.services.tenancy_service import init_live_tenants
        res = init_live_tenants()
        click.echo("Live Tenant Initialization Complete:")
        click.echo(f"  Tenant 0: {res['tenant0']['name']} -> {res['tenant0']['domain']}")
        click.echo(f"  Tenant 1: {res['tenant1']['name']} -> {res['tenant1']['domain']}")
        click.echo(f"  Suspended {res['suspended_count']} other organization(s).")

    @app.cli.command("seed-recruiter-demo")
    def seed_recruiter_demo_cmd():
        """Idempotently seeds recruiter demo data, treks, and sample bookings."""
        from app.services.tenancy_service import seed_recruiter_demo
        res = seed_recruiter_demo()
        click.echo("Recruiter Demo Seeding Complete:")
        click.echo(f"  Platform Super Admin: {res['platform_admin']}")
        click.echo(f"  Tenant 0: {res['tenant0']['name']} ({res['tenant0']['domain']})")
        click.echo(f"  Tenant 1: {res['tenant1']['name']} ({res['tenant1']['domain']})")


    @app.cli.command("create-tenant")
    @click.option("--name", required=True, help="Organization name")
    @click.option("--subdomain", required=True, help="Subdomain prefix")
    @click.option("--admin-email", required=True, help="Tenant admin email")
    @click.option("--admin-name", default="Admin", help="Tenant admin name")
    @click.option("--theme", default=None, help="Custom UI theme folder name or directory")
    def create_tenant(name, subdomain, admin_email, admin_name, theme):
        """Provisions a new tenant organization and initial administrator."""
        from app.models.platform_admin import PlatformAdmin
        from app.services import tenancy_service
        from app.tenancy.scoping import tenant_bypass

        with tenant_bypass():
            creator = PlatformAdmin.query.first()
            if not creator:
                raise click.ClickException("No Platform Admin found. Run 'flask seed-platform-admin' first.")

            try:
                org, admin_user, temp_password = tenancy_service.create_organization(
                    name=name,
                    subdomain=subdomain,
                    tenant_admin_email=admin_email,
                    tenant_admin_name=admin_name,
                    platform_admin=creator,
                    theme=theme,
                )
                click.echo(f"Tenant '{org.name}' created successfully (id={org.id}).")
                if org.theme:
                    click.echo(f"Custom UI Theme: {org.theme}")
                click.echo(f"Primary Domain: {subdomain}.{os.environ.get('BASE_DOMAIN', 'localhost')}")
                click.echo(f"Tenant Admin: {admin_user.email}")
                click.echo(f"Temporary Password (shown once): {temp_password}")
            except ValueError as err:
                raise click.ClickException(str(err))

    @app.cli.command("seed-rbac")
    def seed_rbac():
        """Seeds the global capability catalog, starter roles, and backfills legacy staff."""
        from app.models.organization import Organization
        from app.services import rbac_service
        from app.tenancy.scoping import tenant_bypass

        with tenant_bypass():
            catalog_count = rbac_service.seed_catalog()
            click.echo(f"Seeded global capability catalog ({catalog_count} new entries checked).")

            orgs = Organization.query.all()
            for org in orgs:
                rbac_service.seed_starter_roles(org)

            backfill_count = rbac_service.backfill_staff_roles()
            click.echo(f"Seeded starter roles across {len(orgs)} organization(s).")
            click.echo(f"Backfilled {backfill_count} legacy staff profile(s) with 'Trek Coordinator' role.")

    @app.cli.command("backfill-consents")
    def backfill_consents_command():
        """Backfills transactional EMAIL consent for legacy users across all tenants."""
        from app.services import auth_service
        count = auth_service.backfill_consents()
        click.echo(f"Successfully backfilled transactional email consent for {count} user(s).")

    @app.cli.command("seed-tenant-zero")
    def seed_tenant_zero():
        """Idempotently seeds Tenant Zero organization and its primary subdomain."""
        from app.models.organization import Organization
        from app.models.domain import Domain
        from app.models.platform_admin import PlatformAdmin
        from app.tenancy.scoping import tenant_bypass

        name = os.environ.get("TENANT_ZERO_NAME", "Summit Expeditions")
        subdomain = os.environ.get("TENANT_ZERO_SUBDOMAIN", "tenantzero")
        base_domain = os.environ.get("BASE_DOMAIN", "localhost")
        full_domain = f"{subdomain}.{base_domain}"

        with tenant_bypass():
            existing_domain = db.session.execute(
                select(Domain).where(Domain.domain_name == full_domain)
            ).scalar_one_or_none()

            creator = PlatformAdmin.query.first()
            if not creator:
                p_email = os.environ.get("PLATFORM_ADMIN_EMAIL", "owner@platform.local").lower().strip()
                p_name = os.environ.get("PLATFORM_ADMIN_NAME", "Platform Owner")
                p_pass = os.environ.get("PLATFORM_ADMIN_PASSWORD", "change-me-platform-password")
                creator = PlatformAdmin(name=p_name, email=p_email, is_active=True)
                creator.set_password(p_pass)
                db.session.add(creator)
                db.session.flush()

            if existing_domain:
                click.echo(
                    f"Tenant Zero domain '{full_domain}' already exists (org_id={existing_domain.organization_id}). Skipping."
                )
                return

            existing_org = db.session.execute(
                select(Organization).where(Organization.name == name)
            ).scalar_one_or_none()

            if not existing_org:
                org = Organization(
                    name=name,
                    status="active",
                    created_by_platform_admin_id=creator.id,
                )
                db.session.add(org)
                db.session.flush()
            else:
                org = existing_org
                if not org.created_by_platform_admin_id:
                    org.created_by_platform_admin_id = creator.id
                    db.session.flush()

            domain = Domain(
                organization_id=org.id,
                domain_name=full_domain,
                domain_type="subdomain",
                is_primary=True,
            )
            db.session.add(domain)
            db.session.commit()
            click.echo(f"Seeded Tenant Zero: org_id={org.id} domain={full_domain}")

    @app.cli.command("seed-admin")
    def seed_admin():
        """Seeds the database with the initial Admin user for Tenant Zero if one does not already exist."""
        from app.models.organization import Organization
        from app.models.domain import Domain
        from app.models.user import User
        from app.tenancy.scoping import tenant_context, tenant_bypass

        admin_email = app.config.get("ADMIN_EMAIL")
        admin_password = app.config.get("ADMIN_PASSWORD")

        if not admin_email or not admin_password:
            click.echo("Error: ADMIN_EMAIL or ADMIN_PASSWORD not found in environment variables.")
            return

        with tenant_bypass():
            tenant_name = os.environ.get("TENANT_ZERO_NAME", "Summit Expeditions")
            org = db.session.execute(
                select(Organization).where(Organization.name == tenant_name)
            ).scalar_one_or_none()
            if not org:
                org = db.session.execute(
                    select(Organization).where(Organization.status == "active")
                ).scalars().first()

        if not org:
            click.echo("Error: No organization found to seed admin into. Run 'flask seed-tenant-zero' first.")
            return

        with tenant_context(org.id):
            existing_admin = db.session.execute(
                select(User).where(User.role == "admin", User.email == admin_email)
            ).scalar_one_or_none()

            if existing_admin:
                existing_admin.password_hash = generate_password_hash(admin_password)
                existing_admin.is_active = True
                with tenant_bypass():
                    if not org.tenant_admin_user_id:
                        org.tenant_admin_user_id = existing_admin.id
                    db.session.commit()
                click.echo(f"Updated Admin user '{admin_email}' password from environment.")
                return


            hashed_password = generate_password_hash(admin_password)
            country_code = os.environ.get("ADMIN_MOBILE_COUNTRY_CODE", "+91")
            mobile_num = os.environ.get("ADMIN_MOBILE_NUMBER", "9876543210")

            new_admin = User(
                organization_id=org.id,
                name="System Admin",
                email=admin_email,
                password_hash=hashed_password,
                role="admin",
                is_active=True,
                mobile_country_code=country_code,
                mobile_number=mobile_num,
                terms_accepted_at=None,  # completion gate forces acceptance
            )
            db.session.add(new_admin)
            db.session.flush()

            with tenant_bypass():
                org.tenant_admin_user_id = new_admin.id
                db.session.commit()

            click.echo(f"Successfully created Admin user: {admin_email} for org: {org.name} ({org.id})")

    @app.cli.command("set-tenant-password")
    @click.option("--email", prompt=True, help="User email address")
    @click.option("--password", prompt=True, hide_input=False, help="New password")
    def set_tenant_password(email, password):
        """Sets or resets the password for any tenant user or admin."""
        from app.models.user import User
        from app.tenancy.scoping import tenant_bypass
        with tenant_bypass():
            user = User.query.filter_by(email=email.strip().lower()).first()
            if not user:
                click.echo(f"Error: User with email '{email}' not found.")
                return
            user.password_hash = generate_password_hash(password)
            user.must_reset_password = False
            user.is_active = True
            db.session.commit()
            click.echo(f"Successfully updated password for '{user.email}' (Org ID: {user.organization_id}).")

    # 9. Error Handlers
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

    # 10. Background Scheduler
    if not app.config.get("SCHEDULER_STARTED"):
        from app.services import payment_service

        def _job_with_ctx(fn):
            def _runner():
                with app.app_context():
                    return fn()
            return _runner

        if scheduler.get_job("expire_stale_bookings") is None:
            scheduler.add_job(
                id="expire_stale_bookings",
                func=_job_with_ctx(payment_service.expire_stale_bookings),
                trigger="interval",
                minutes=15,
            )

        if scheduler.get_job("purge_expired_tickets") is None:
            scheduler.add_job(
                id="purge_expired_tickets",
                func=_job_with_ctx(ticket_service.purge_expired),
                trigger="cron",
                hour=2,
                minute=0,
            )

        if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
            scheduler.start()
        app.config["SCHEDULER_STARTED"] = True

    return app
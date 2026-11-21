from datetime import datetime, timezone
import os
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models.organization import Organization
from app.models.domain import Domain
from app.models.user import User
from app.models.platform_admin import PlatformAdmin
from app.services import rbac_service
from app.tenancy.scoping import tenant_bypass, tenant_context

app = create_app()

def seed_tenant(name, subdomain, admin_email, admin_name, admin_pass, trekker_email, trekker_name, trekker_pass):
    with app.app_context(), tenant_bypass():
        creator = PlatformAdmin.query.first()
        domain_name = f"{subdomain}.localhost"

        existing_dom = Domain.query.filter_by(domain_name=domain_name).first()
        if existing_dom:
            print(f"Domain {domain_name} already exists (org_id={existing_dom.organization_id}).")
            org = Organization.query.get(existing_dom.organization_id)
        else:
            org = Organization.query.filter_by(name=name).first()
            if not org:
                org = Organization(
                    name=name,
                    status="active",
                    created_by_platform_admin_id=creator.id if creator else None,
                )
                db.session.add(org)
                db.session.flush()
                print(f"Created Org {name} ({org.id})")

            dom = Domain(
                organization_id=org.id,
                domain_name=domain_name,
                domain_type="subdomain",
                is_primary=True,
                verified_at=datetime.now(timezone.utc),
            )
            db.session.add(dom)
            db.session.commit()
            print(f"Created Domain {domain_name}")

        rbac_service.seed_starter_roles(org)

        # Seed Admin
        with tenant_context(org.id):
            admin = User.query.filter_by(email=admin_email).first()
            if not admin:
                admin = User(
                    organization_id=org.id,
                    name=admin_name,
                    email=admin_email,
                    password_hash=generate_password_hash(admin_pass),
                    role="admin",
                    is_active=True,
                    mobile_country_code="+91",
                    mobile_number="9876543210",
                    terms_accepted_at=datetime.now(timezone.utc),
                )
                db.session.add(admin)
                db.session.flush()
                print(f"Created Admin {admin.email}")
            else:
                admin.password_hash = generate_password_hash(admin_pass)
                admin.terms_accepted_at = datetime.now(timezone.utc)
                admin.is_active = True

            with tenant_bypass():
                org.tenant_admin_user_id = admin.id
                db.session.commit()

            # Seed Trekker
            trekker = User.query.filter_by(email=trekker_email).first()
            if not trekker:
                trekker = User(
                    organization_id=org.id,
                    name=trekker_name,
                    email=trekker_email,
                    password_hash=generate_password_hash(trekker_pass),
                    role="trekker",
                    is_active=True,
                    mobile_country_code="+91",
                    mobile_number="9876543211",
                    terms_accepted_at=datetime.now(timezone.utc),
                )
                db.session.add(trekker)
                db.session.commit()
                print(f"Created Trekker {trekker.email}")
            else:
                trekker.password_hash = generate_password_hash(trekker_pass)
                trekker.terms_accepted_at = datetime.now(timezone.utc)
                trekker.is_active = True
                db.session.commit()

if __name__ == "__main__":
    seed_tenant(
        name="Tenant A",
        subdomain="a",
        admin_email="admin@a.localhost",
        admin_name="Admin A",
        admin_pass="Password123!",
        trekker_email="trekker@a.localhost",
        trekker_name="Trekker A",
        trekker_pass="Password123!",
    )
    seed_tenant(
        name="Tenant B",
        subdomain="b",
        admin_email="admin@b.localhost",
        admin_name="Admin B",
        admin_pass="Password123!",
        trekker_email="trekker@b.localhost",
        trekker_name="Trekker B",
        trekker_pass="Password123!",
    )
    print("Seeding of Tenant A and Tenant B complete!")

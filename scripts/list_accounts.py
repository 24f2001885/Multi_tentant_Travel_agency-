from app import create_app, db
from app.models.organization import Organization
from app.models.domain import Domain
from app.models.user import User
from app.models.platform_admin import PlatformAdmin
from app.tenancy.scoping import tenant_bypass

app = create_app()
with app.app_context(), tenant_bypass():
    print("=== PLATFORM ADMINS ===")
    for p in PlatformAdmin.query.all():
        print(f"ID: {p.id} | Email: {p.email} | Name: {p.name} | Active: {p.is_active}")

    print("\n=== ORGANIZATIONS ===")
    for org in Organization.query.all():
        print(f"Org ID: {org.id} | Name: {org.name} | Theme: {org.theme} | Primary: {org.primary_color} | Secondary: {org.secondary_color} | Status: {org.status} | Tenant Admin ID: {org.tenant_admin_user_id}")

    from app.models.trek import Trek
    from app.services.id_codec import encode
    print("\n=== TREKS ===")
    for t in Trek.query.all():
        print(f"Trek ID: {t.id} | HashID: {encode(t.id)} | Org ID: {t.organization_id} | Name: {t.name} | Status: {t.status} | Price: {t.price}")

    print("\n=== DOMAINS ===")
    for d in Domain.query.all():
        print(f"Domain: {d.domain_name} | Org ID: {d.organization_id} | Primary: {d.is_primary}")

    print("\n=== USERS ===")
    for u in User.query.all():
        print(f"User ID: {u.id} | Org ID: {u.organization_id} | Email: {u.email} | Role: {u.role} | Active: {u.is_active}")

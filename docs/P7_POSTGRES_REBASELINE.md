# PostgreSQL Re-baseline Runbook (Milestone P7)

This runbook guides the database migration from SQLite to PostgreSQL 15 for the TMA Multi-Tenant Platform (TDR v2 §9, Blueprint v2 P7).

---

## 1. Archive Existing SQLite Migrations
Move historical SQLite migration versions to an archive folder so Alembic starts fresh against PostgreSQL without schema conflicts:
```bash
mkdir -p migrations/_archive_sqlite
mv migrations/versions/*.py migrations/_archive_sqlite/ 2>/dev/null || true
```
Keep `migrations/env.py` and `migrations/alembic.ini`.

---

## 2. Generate v2 PostgreSQL Baseline
Ensure the PostgreSQL container is running and healthy:
```bash
docker compose up -d db
```

Generate the initial PostgreSQL migration script:
```bash
docker compose run --rm web flask db migrate -m "v2 multi-tenant postgres baseline"
```

### Verification Checklist on Generated Migration:
- [ ] UUID primary keys on `organizations`, `domains`, and `call_requests`.
- [ ] `organization_id` foreign key (`NOT NULL`, indexed) present on all tenant-scoped tables (`users`, `staff_profiles`, `treks`, `bookings`, `payments`, `tickets`, `password_reset_tokens`, `audit_logs`).
- [ ] `postgresql_where=text("status IN ('pending', 'booked')")` on `uq_active_booking` in `bookings`.
- [ ] Composite unique constraint `UniqueConstraint('organization_id', 'email', name='uq_users_org_email')` on `users`.
- [ ] Circular foreign key between `organizations.tenant_admin_user_id` and `users.id` created via `use_alter=True`.
- [ ] Timezone-aware timestamp columns (`TIMESTAMPTZ` / `DateTime(timezone=True)`).

---

## 3. Database Initialization Options

### Path A: Fresh Development Database (Recommended)
Apply the baseline migration and seed Tenant Zero and Admin:
```bash
docker compose run --rm web flask db upgrade
docker compose run --rm web flask seed-tenant-zero
docker compose run --rm web flask seed-admin
```

### Path B: Migrating Existing Single-Tenant SQLite Data
1. Export existing SQLite tables to JSON or CSV.
2. Run `flask db upgrade` on the fresh PostgreSQL database.
3. Run `flask seed-tenant-zero` to establish Tenant Zero (`tenantzero.localhost`).
4. Import rows in strict foreign-key order:
   1. `users` (stamped with Tenant Zero `organization_id`, `mobile_country_code='+91'`, `mobile_number='UNSET'`, `terms_accepted_at=NULL`)
   2. `staff_profiles`
   3. `treks`
   4. `bookings`
   5. `payments`
   6. `tickets`
   7. `password_reset_tokens`
   8. `audit_logs`
5. Link `organizations.tenant_admin_user_id` to the imported Admin user.

---

## 4. Verification Commands
- Check tables in PostgreSQL:
  ```bash
  docker compose exec db psql -U tma -d tma_platform -c '\dt'
  ```
- Test migration round-trip:
  ```bash
  docker compose run --rm web flask db downgrade base
  docker compose run --rm web flask db upgrade
  ```

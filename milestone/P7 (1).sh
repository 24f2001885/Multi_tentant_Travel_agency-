#!/usr/bin/env bash
###############################################################################
# P7.sh — v2 MILESTONE 7: Platform Foundation
# TMA Platform — Multi-Tenant Trekking Management SaaS
#
# Covers (Master Project Blueprint v2 §3, phase P7):
#   - Postgres migration (re-baselined schema, UUID PKs on new tables)
#   - organizations / domains, TenantScopedMixin + loader-criteria filter,
#     per-request tenant resolver
#   - Public (no-login) landing / browse / trek detail
#   - Opaque identifiers (Hashids at the HTTP boundary)
#   - Mandatory country-code mobile, T&C + cookie consent, footer,
#     Request-a-Call form
#
# Depends on: P0–P6 (delivered). Never writes real application logic — only
# git/dir/config setup + PROMPT seeds for the code-implementer skill.
#
# FLAGGED DECISIONS (read before running):
#  1. NUMBERING CLASH: an older P7.sh/p7_milestone.log (charts/validation/
#     responsive) exists = v2's P14 work. If you still have that old
#     docs/P7.sh, rename it P14.sh BEFORE saving this file. This run logs to
#     docs/p7_v2_milestone.log and branches from the old P7 branch if it
#     exists, else from phase/P6-enhancements-hardening.
#  2. LOCKING: PDR v2 §10 puts the Postgres locking re-derivation in P7,
#     Blueprint v2 puts it in P9. `BEGIN IMMEDIATE` is a syntax error on
#     Postgres, so P7 does a minimal 1:1 swap to SELECT ... FOR UPDATE (no
#     behavior change); P9 still owns group-booking generalization.
#  3. organizations.created_by_platform_admin_id is NULLABLE here (TDR v2
#     §4.1 says NOT NULL FK) because platform_admins only exists from P8.
#     P8 tightens it.
#  4. users.email is UNIQUE per organization (organization_id, email), not
#     globally — TDR v2 omits this; a global unique would let tenant A block
#     tenant B's customers and leak account existence across tenants.
#  5. users.terms_accepted_at is NULLABLE (TDR v2 §4.3 says NOT NULL): legacy
#     rows and staff-created accounts cannot have accepted terms. NULL (or a
#     legacy-sentinel mobile number) triggers a "complete your account" gate.
#  6. Scoped queries FAIL CLOSED (no tenant context => error). tenant_bypass()
#     is allow-listed to: resolver domain lookup, Platform Admin, webhook org
#     derivation, scheduler jobs, CLI. A test enforces the allow-list.
#  7. Files beyond the Blueprint tree (needed to make it work, no new
#     top-level folders): services/id_codec.py, trekker/landing.html,
#     trekker/legal.html, shared/_cookie_consent.html,
#     auth/complete_account.html, docs/P7_POSTGRES_REBASELINE.md.
#  8. New env vars beyond TDR v2 §16: BASE_DOMAIN, PLATFORM_ADMIN_HOST,
#     TENANT_ZERO_*, ADMIN_MOBILE_*, DEFAULT_MOBILE_REGION, HASHIDS_MIN_LENGTH.
#  9. New dependency: `phonenumbers` (country-code-aware validation).
# 10. v1 DateTime columns are naive-UTC; new TDR v2 tables use TIMESTAMPTZ.
#     Mixing aware/naive in comparisons is a footgun — see the re-baseline
#     doc for the recommended all-aware conversion.
#
# USAGE:
#   cd ~/Desktop/t
#   bash ./docs/P7.sh
###############################################################################

set -euo pipefail

BRANCH_NAME="phase/P7-platform-foundation"
LOG_FILE="./docs/p7_v2_milestone.log"

mkdir -p ./docs
touch "$LOG_FILE"

log() {
  local ts; ts="$(date '+%Y-%m-%d %H:%M:%S')"
  echo "[$ts] $*"; echo "[$ts] $*" >> "$LOG_FILE"
}

SEED_FILES=()
seed_note() {
  local abspath; abspath="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
  echo "  -> Seed file created: $abspath"
  log "SEED FILE created: $abspath"
  SEED_FILES+=("$abspath")
}

ensure_req() {
  local pkg_line="$1" pkg_name
  pkg_name="$(echo "$pkg_line" | sed -E 's/[<>=!~].*//')"
  if grep -qi "^${pkg_name}" requirements.txt 2>/dev/null; then
    echo "  -> $pkg_name already in requirements.txt, skipping"
  else
    echo "$pkg_line" >> requirements.txt
    log "requirements.txt: appended $pkg_line"
  fi
}

checkout_phase_branch() {
  local base="$1" target="$2"
  if git show-ref --verify --quiet "refs/heads/$target"; then
    git checkout "$target"
  else
    git checkout "$base"
    git checkout -b "$target"
  fi
}

trap 'log "ERROR: script aborted unexpectedly at line $LINENO"' ERR
log "===== P7.sh (v2) started — Platform Foundation ====="

# -----------------------------------------------------------------------------
# STEP 0: Pre-flight
# -----------------------------------------------------------------------------
echo "STEP 0: Pre-flight checks..."
[ -d ".git" ] || { echo "ERROR: no .git found."; exit 1; }
for d in app/models app/auth app/admin app/staff app/trekker app/tickets app/api; do
  [ -d "$d" ] || { echo "ERROR: $d missing — run P0–P6 first."; exit 1; }
done
for f in app/services/payment_service.py app/services/booking_service.py \
         app/services/ticket_service.py app/services/trek_service.py \
         app/services/auth_service.py app/api/treks_resource.py app/api/payments_resource.py; do
  [ -f "$f" ] || { echo "ERROR: $f missing — run P0–P6 first."; exit 1; }
done
log "STEP 0 complete: pre-flight checks passed"

# -----------------------------------------------------------------------------
# STEP 1: Branch
# -----------------------------------------------------------------------------
echo "STEP 1: Checking out milestone branch..."
if git show-ref --verify --quiet refs/heads/phase/P7-charts-validation-responsive; then
  BASE_BRANCH="phase/P7-charts-validation-responsive"
else
  BASE_BRANCH="phase/P6-enhancements-hardening"
fi
checkout_phase_branch "$BASE_BRANCH" "$BRANCH_NAME"
log "STEP 1 complete: on $BRANCH_NAME (base: $BASE_BRANCH)"

# -----------------------------------------------------------------------------
# STEP 2: Directories
# -----------------------------------------------------------------------------
echo "STEP 2: Creating directories..."
mkdir -p app/tenancy app/public tests/unit tests/integration
log "STEP 2 complete: app/tenancy, app/public created"

# -----------------------------------------------------------------------------
# STEP 3: Infra/config (final content — config, not application logic)
# -----------------------------------------------------------------------------
echo "STEP 3: Writing Postgres infra/config..."
[ -f docker-compose.yml ] && cp docker-compose.yml docker-compose.v1.yml.bak

cat << 'SEEDEOF' > docker-compose.yml
services:
  web:
    build: .
    ports: ["5000:5000"]
    env_file: .env
    environment:
      - DATABASE_URL=postgresql://tma:tma_dev_password@db:5432/tma_platform
    volumes:
      - .:/app
      - ./instance:/app/instance
    depends_on:
      db:
        condition: service_healthy
      mailhog:
        condition: service_started

  db:
    image: postgres:15
    environment:
      POSTGRES_USER: tma
      POSTGRES_PASSWORD: tma_dev_password
      POSTGRES_DB: tma_platform
    ports: ["5432:5432"]
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U tma -d tma_platform"]
      interval: 3s
      timeout: 3s
      retries: 20

  db_test:
    image: postgres:15
    environment:
      POSTGRES_USER: tma
      POSTGRES_PASSWORD: tma_dev_password
      POSTGRES_DB: tma_platform_test
    tmpfs:
      - /var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U tma -d tma_platform_test"]
      interval: 3s
      timeout: 3s
      retries: 20

  mailhog:
    image: mailhog/mailhog
    ports: ["8025:8025", "1025:1025"]

  test:
    build: .
    env_file: .env
    environment:
      - FLASK_ENV=testing
      - DATABASE_URL=postgresql://tma:tma_dev_password@db_test:5432/tma_platform_test
      - TEST_DATABASE_URL=postgresql://tma:tma_dev_password@db_test:5432/tma_platform_test
    volumes:
      - .:/app
    depends_on:
      db_test:
        condition: service_healthy
    entrypoint: ["pytest", "-v"]

  # redis:            # RESERVED — deliberately NOT activated this phase (TDR v2 §15)
  #   image: redis:7

volumes:
  pgdata:
SEEDEOF
seed_note "docker-compose.yml"

ensure_req "psycopg2-binary>=2.9,<3.0"
ensure_req "hashids>=1.3,<2.0"
ensure_req "phonenumbers>=8.13,<9.0"
seed_note "requirements.txt (P7: psycopg2-binary, hashids, phonenumbers)"

# .env.example: repoint DB urls at Postgres, append v2 block once
sed -i -E 's|^DATABASE_URL=.*|DATABASE_URL=postgresql://tma:tma_dev_password@db:5432/tma_platform|; s|^TEST_DATABASE_URL=.*|TEST_DATABASE_URL=postgresql://tma:tma_dev_password@db_test:5432/tma_platform_test|' .env.example
if ! grep -q "TMA Platform v2 (P7)" .env.example; then
cat << 'SEEDEOF' >> .env.example

# --- TMA Platform v2 (P7) ---
HASHIDS_SALT=change-me-per-deployment
HASHIDS_MIN_LENGTH=8
REDIS_URL=
BASE_DOMAIN=localhost
PLATFORM_ADMIN_HOST=platform.localhost
TENANT_ZERO_NAME=Tenant Zero Treks
TENANT_ZERO_SUBDOMAIN=tenantzero
ADMIN_MOBILE_COUNTRY_CODE=+91
ADMIN_MOBILE_NUMBER=9999999999
DEFAULT_MOBILE_REGION=IN
SEEDEOF
fi
seed_note ".env.example"
echo "  !! Copy the new keys from .env.example into your real .env before running."
log "STEP 3 complete: docker-compose.yml (Postgres), requirements.txt, .env.example"

# -----------------------------------------------------------------------------
# STEP 3b: Re-baseline runbook (a .md — NOT a .py in migrations/versions, since
# a docstring-only file there would break Alembic's script loader)
# -----------------------------------------------------------------------------
cat << 'SEEDEOF' > docs/P7_POSTGRES_REBASELINE.md
<!--
FILE PURPOSE: One-time SQLite -> Postgres re-baseline runbook (TDR v2 §9,
Blueprint v2 P7). Replaces this comment with a real, ordered runbook.

PROMPT FOR LLM IMPLEMENTATION:
1. Step "Archive": move migrations/versions/*.py to migrations/_archive_sqlite/
   (outside Alembic's scan path); keep migrations/env.py.
2. Step "Baseline": with an EMPTY Postgres (docker compose up -d db), run
   `flask db migrate -m "v2 baseline"`; review the generated script for:
   UUID PKs on organizations/domains/call_requests, postgresql_where on the
   bookings partial unique index (uq_active_booking), CHECK constraints
   preserved, users UNIQUE(organization_id, email), the
   organizations<->users circular FK created via use_alter (organizations
   first, FK added after), and organization_id NOT NULL on all 8 v1 tables.
3. Path A — fresh dev DB (recommended, no legacy data): `flask db upgrade`,
   `flask seed-tenant-zero`, `flask seed-admin`.
4. Path B — keep existing SQLite data: export each v1 table, import in FK
   order (users, staff_profiles, treks, bookings, payments, tickets,
   password_reset_tokens, audit_logs) into Postgres AFTER
   `flask seed-tenant-zero`, stamping every row with tenant zero's
   organization_id; give every legacy user mobile_country_code='+91',
   mobile_number='UNSET' (the legacy sentinel) and terms_accepted_at=NULL so
   the complete-account gate forces real values at next login; set
   organizations.tenant_admin_user_id to the existing admin user.
5. Step "Datetimes" (decision): recommended — convert all v1 DateTime columns
   to timezone-aware (TIMESTAMPTZ, existing values assumed UTC) in the
   baseline and switch models' default=utcnow to an aware-UTC helper, so v1
   and v2 tables never mix naive/aware values. Record the decision taken.
6. Step "Verify": row counts per table match the source; every NOT NULL /
   CHECK / UNIQUE constraint present (`\d+ table` in psql); `flask db
   downgrade base` then `upgrade head` round-trips cleanly.

DEBUGGING:
docker compose exec db psql -U tma -d tma_platform -c '\dt'

OFFLINE DOCKER TEST CASES:
- `flask db upgrade` on an empty Postgres creates all 8 v1 tables plus
  organizations, domains, call_requests (11 total + alembic_version).
- `flask db downgrade base` followed by `flask db upgrade` leaves the schema
  identical.
-->
SEEDEOF
seed_note "docs/P7_POSTGRES_REBASELINE.md"

# -----------------------------------------------------------------------------
# STEP 4: app/tenancy — resolver + scoping
# -----------------------------------------------------------------------------
echo "STEP 4: Seeding app/tenancy..."

cat << 'SEEDEOF' > app/tenancy/__init__.py
"""
FILE PURPOSE: Package surface for tenant resolution and query scoping
(TDR v2 §2, §6). A request-lifecycle module, not a blueprint.

PROMPT FOR LLM IMPLEMENTATION:
1. Re-export: `init_tenancy` (resolver.py); `TenantScopedMixin`,
   `tenant_context`, `tenant_bypass`, `TenantContextMissingError`,
   `TenantMismatchError` (scoping.py).
2. No side effects at import time; importable with no app context.

DEBUGGING:
# print("[DEBUG] app.tenancy loaded")

OFFLINE DOCKER TEST CASES:
- `from app.tenancy import TenantScopedMixin, tenant_context, tenant_bypass,
  init_tenancy` succeeds with no Flask app context.
"""
SEEDEOF
seed_note "app/tenancy/__init__.py"

cat << 'SEEDEOF' > app/tenancy/resolver.py
"""
FILE PURPOSE: The per-request tenant resolver: host -> Domain ->
Organization -> g.organization_id (TDR v2 §2). Runs before any route logic.

PROMPT FOR LLM IMPLEMENTATION:
1. `init_tenancy(app)` registers one `before_request` hook, `resolve_tenant`.
2. Normalize `request.host`: lowercase, strip port. Use request.host ONLY —
   never trust X-Tenant-style headers or query params (proxy handling is a
   deployment concern via ProxyFix, not this module).
3. Skip resolution (leave g.organization_id UNSET) for: endpoint `static`;
   host == env PLATFORM_ADMIN_HOST (Platform Admin control plane, blueprint
   arrives P8); and a module-level EXEMPT_PATHS tuple holding
   `/api/payments/webhook` and `/api/payments/route-webhook` (gateway
   callbacks carry no tenant host — handlers derive the org from the
   order-id chain, TDR v2 §3.3).
4. Look up `domains.domain_name` inside `tenant_bypass()` (Domain and
   Organization are not tenant-scoped). `domain_type='custom'` requires
   `verified_at IS NOT NULL`; subdomains route immediately.
5. No matching domain -> `abort(404)` before any route code. Matching org
   with `status != 'active'` -> render templates/errors/generic.html with
   code=403 and message "This organization is currently unavailable."
6. On success set `g.organization_id` (UUID) and `g.organization` (the
   Organization row, used for branding in templates).
7. No in-process caching of host->org this phase (DB lookup per request;
   `domains.domain_name` is UNIQUE-indexed).

DEBUGGING:
# print(f"[DEBUG] tenant resolve host={host} -> org={getattr(g,'organization_id',None)}")

OFFLINE DOCKER TEST CASES:
- Unknown host -> 404; unverified custom domain -> 404; suspended org -> 403.
- Host with a port (tenant.localhost:5000) resolves identically to without.
- A request to PLATFORM_ADMIN_HOST leaves g.organization_id unset.
- POST /api/payments/webhook on a non-tenant host is not 404'd by the resolver.
"""
SEEDEOF
seed_note "app/tenancy/resolver.py"

cat << 'SEEDEOF' > app/tenancy/scoping.py
"""
FILE PURPOSE: TenantScopedMixin + the SQLAlchemy global filter that makes
cross-tenant reads/writes structurally impossible (TDR v2 §6) — the single
most important security mechanism in the platform.

PROMPT FOR LLM IMPLEMENTATION:
1. `TenantScopedMixin`: `organization_id` via @declared_attr — UUID, FK
   organizations.id, NOT NULL, indexed (each table gets its own FK).
2. Register a session-level `do_orm_execute` listener on db.session adding
   `with_loader_criteria(TenantScopedMixin, organization_id == current org,
   include_aliases=True)` to every ORM SELECT/UPDATE/DELETE (also covers
   relationship lazy loads) unless bypassed.
3. Register a `before_flush` listener: new TenantScopedMixin objects with
   organization_id None are auto-filled from the current org; if already set
   and different from the current org raise TenantMismatchError (skipped
   under bypass).
4. FAIL CLOSED: no current org and no bypass -> raise
   TenantContextMissingError. Current org is read from flask.g (requires an
   app context; jobs/CLI must push one).
5. `tenant_context(org_id)` context manager: temporarily sets
   g.organization_id (tests, CLI, per-org jobs).
6. `tenant_bypass()` context manager: disables criteria + auto-fill inside
   the block; nesting-safe. Allow-list of callers: resolver domain lookup,
   Platform Admin routes/services (P8), webhook org derivation,
   scheduler maintenance jobs, CLI commands. Nothing else may import it.
7. Document the known blind spots in the module docstring: raw `text()`/Core
   statements are NOT filtered (any such code must scope manually), and
   unscoped models are exactly: Organization, Domain, PlatformAdmin,
   PlatformAccessLog, Capability.

DEBUGGING:
# print(f"[DEBUG] scoping active org={getattr(g,'organization_id',None)} bypass={_bypass_active()}")

OFFLINE DOCKER TEST CASES:
- A Trek created under org A is invisible to a query under org B, and
  db.session.get(Trek, id_of_A_trek) under org B returns None.
- Creating a TenantScopedMixin object with no explicit organization_id under
  tenant_context(A) persists organization_id == A.
- Any scoped query with no tenant context raises TenantContextMissingError.
- Inside tenant_bypass() both orgs' rows are visible; after exit, filtering
  resumes (nesting two bypasses and exiting one keeps bypass active).
- A new object whose organization_id differs from the current org raises
  TenantMismatchError at flush.
"""
SEEDEOF
seed_note "app/tenancy/scoping.py"
log "STEP 4 complete: app/tenancy seeded"

# -----------------------------------------------------------------------------
# STEP 5: Models — new (UUID PK) + retrofit addenda
# -----------------------------------------------------------------------------
echo "STEP 5: Seeding models..."

cat << 'SEEDEOF' > app/models/organization.py
"""
FILE PURPOSE: The `organizations` table — one row per tenant (trekking
company), carrying branding, legal text and payout linkage. NOT tenant-scoped
(it IS the tenant). Source of truth: TDR v2 §4.1.

PROMPT FOR LLM IMPLEMENTATION:
1. Columns: id (UUID PK, default uuid4), name (String(150) NOT NULL), status
   (String(20) NOT NULL default 'active', CHECK IN ('active','suspended')),
   logo_url (String(500) null), primary_color/secondary_color (String(7)
   null, hex), contact_email (String(255) null), contact_phone (String(20)
   null), legal_terms_text/legal_privacy_text (Text null), gst_number
   (String(20) null), razorpay_route_account_id (String(100) null),
   tenant_admin_user_id (Integer FK users.id null, use_alter=True —
   organizations<->users is circular), created_by_platform_admin_id (UUID
   null, NO FK yet — FLAGGED: P8 adds the FK + NOT NULL once platform_admins
   exists), created_at (DateTime(timezone=True) NOT NULL default now).
2. Relationships: domains (one-to-many).
3. `branding()` returns a plain dict {name, logo_url, primary_color,
   secondary_color, contact_email, contact_phone, legal_terms_text,
   legal_privacy_text} — the exact data templates/frontends consume.
4. NOT a TenantScopedMixin model.

DEBUGGING:
# print(f"[DEBUG] organization {self.id} name={self.name} status={self.status}")

OFFLINE DOCKER TEST CASES:
- Inserting status='archived' raises IntegrityError (CHECK).
- branding() contains exactly the 8 keys listed and no payout/GST fields.
- create_all() succeeds on Postgres despite the organizations<->users
  circular FK.
"""
SEEDEOF
seed_note "app/models/organization.py"

cat << 'SEEDEOF' > app/models/domain.py
"""
FILE PURPOSE: The `domains` table — subdomain/custom-domain -> organization
map used by the tenant resolver. NOT tenant-scoped. TDR v2 §4.1.

PROMPT FOR LLM IMPLEMENTATION:
1. Columns: id (UUID PK), organization_id (UUID FK organizations.id NOT NULL
   — plain column here, NOT the mixin), domain_name (String(255) NOT NULL
   UNIQUE, lower-cased via a @validates hook), domain_type (String(20) NOT
   NULL, CHECK IN ('subdomain','custom')), is_primary (Boolean NOT NULL
   default True), verified_at (DateTime(timezone=True) null).
2. Property `is_routable`: subdomain -> True; custom -> verified_at is not
   None (custom domains need DNS verification before routing).

DEBUGGING:
# print(f"[DEBUG] domain {self.domain_name} type={self.domain_type} routable={self.is_routable}")

OFFLINE DOCKER TEST CASES:
- Two Domain rows with the same domain_name (any case) raise IntegrityError.
- A custom domain with verified_at None has is_routable == False.
"""
SEEDEOF
seed_note "app/models/domain.py"

cat << 'SEEDEOF' > app/models/call_request.py
"""
FILE PURPOSE: The `call_requests` table — footer "Request a Call" submissions
from anonymous or logged-in visitors. TDR v2 §4.5.

PROMPT FOR LLM IMPLEMENTATION:
1. Inherit TenantScopedMixin. Columns: id (UUID PK), name (String(150) NOT
   NULL), phone (String(20) NOT NULL), preferred_time (String(100) null),
   message (Text null), status (String(10) NOT NULL default 'new', CHECK IN
   ('new','contacted','closed')), handled_by (Integer FK users.id null),
   created_at (DateTime(timezone=True) NOT NULL default now).
2. Index (organization_id, status, created_at) for the admin queue.

DEBUGGING:
# print(f"[DEBUG] call_request {self.id} status={self.status}")

OFFLINE DOCKER TEST CASES:
- status defaults to 'new'; status='pending' raises IntegrityError.
- A CallRequest created under org A is invisible under org B.
"""
SEEDEOF
seed_note "app/models/call_request.py"

cat << 'SEEDEOF' >> app/models/__init__.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend this module):
1. Import and add to __all__: Organization, Domain, CallRequest.
2. Keep every earlier export unchanged.

DEBUGGING:
# print(f"[DEBUG] models package now exports {len(__all__)} models")

OFFLINE DOCKER TEST CASES:
- db.metadata.tables contains the 8 v1 tables plus organizations, domains,
  call_requests (11).
"""
SEEDEOF
seed_note "app/models/__init__.py (P7 addendum)"

cat << 'SEEDEOF' >> app/models/user.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (modify this model):
1. Inherit TenantScopedMixin (organization_id NOT NULL).
2. Add mobile_country_code (String(5) NOT NULL), mobile_number (String(20)
   NOT NULL), terms_accepted_at (DateTime(timezone=True) NULLABLE — FLAGGED
   deviation from TDR v2's NOT NULL: legacy and staff-created accounts cannot
   have accepted terms).
3. Replace the global UNIQUE on email with UniqueConstraint(organization_id,
   email, name='uq_users_org_email'); lower-case email via @validates.
4. Module constant LEGACY_MOBILE_SENTINEL = 'UNSET' for backfilled rows.
5. Property `needs_account_completion`: True when terms_accepted_at is None
   OR mobile_number == LEGACY_MOBILE_SENTINEL.

DEBUGGING:
# print(f"[DEBUG] user {self.id} org={self.organization_id} needs_completion={self.needs_account_completion}")

OFFLINE DOCKER TEST CASES:
- The same email under org A and org B both insert; twice under org A raises
  IntegrityError.
- needs_account_completion is True for terms_accepted_at=None and for
  mobile_number='UNSET'; False once both are real.
"""
SEEDEOF
seed_note "app/models/user.py (P7 addendum)"

cat << 'SEEDEOF' >> app/models/booking.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (modify this model):
1. Inherit TenantScopedMixin (organization_id NOT NULL).
2. The partial unique index uq_active_booking(user_id, trek_id) currently
   uses sqlite_where only — add `postgresql_where=text("status IN
   ('pending','booked')")` (keep both kwargs). Slot logic stays untouched
   (slot_count etc. land in P9).

DEBUGGING:
# print(f"[DEBUG] booking {self.id} org={self.organization_id} status={self.status}")

OFFLINE DOCKER TEST CASES:
- On Postgres, two bookings with status IN ('pending','booked') for the same
  user+trek raise IntegrityError; a 'cancelled' one does not.
"""
SEEDEOF
seed_note "app/models/booking.py (P7 addendum)"

# Mixin-only retrofits (same text appended to each model)
for f in app/models/staff_profile.py app/models/trek.py app/models/payment.py \
         app/models/ticket.py app/models/password_reset_token.py app/models/audit_log.py; do
cat << 'SEEDEOF' >> "$f"

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (modify this model):
1. Inherit TenantScopedMixin from app.tenancy.scoping (adds organization_id
   UUID FK organizations.id NOT NULL, indexed). No other column changes in
   this phase. Existing integer PKs/FKs stay as they are (opaque IDs are an
   HTTP-boundary concern only, TDR v2 §1).
2. Do not hand-set organization_id in callers — the before_flush hook fills
   it from the current tenant context.

DEBUGGING:
# print(f"[DEBUG] {self.__class__.__name__} id={self.id} org={self.organization_id}")

OFFLINE DOCKER TEST CASES:
- A row created under tenant_context(A) is invisible under tenant_context(B).
- Inserting with organization_id=None outside any tenant context raises
  TenantContextMissingError.
"""
SEEDEOF
seed_note "$f (P7 addendum: TenantScopedMixin)"
done
log "STEP 5 complete: new models + retrofit addenda seeded"

# -----------------------------------------------------------------------------
# STEP 6: Services
# -----------------------------------------------------------------------------
echo "STEP 6: Seeding services..."

cat << 'SEEDEOF' > app/services/id_codec.py
"""
FILE PURPOSE: Reversible opaque-ID encoding for the integer-PK v1 tables
(TDR v2 §6). Framework-agnostic — no Flask objects. NOT in the Blueprint tree
(services/ chosen to avoid a new folder).

PROMPT FOR LLM IMPLEMENTATION:
1. `encode(int_id) -> str` and `decode(token) -> int | None`, backed by the
   hashids library with salt = env HASHIDS_SALT and min_length = env
   HASHIDS_MIN_LENGTH (default 8). Missing salt -> raise at call time (the
   app factory also fails fast at startup); never fall back to a default.
2. `decode` NEVER raises: bad, tampered, or non-string input -> None (callers
   turn None into 404).
3. UUID-keyed (new) tables are exposed raw — do not encode them.

DEBUGGING:
# print(f"[DEBUG] id_codec {int_id} -> {token}")

OFFLINE DOCKER TEST CASES:
- decode(encode(n)) == n for n in 1..1000; encode(1) != "1".
- decode("garbage!!") and decode(None) return None.
- Two different salts produce different encodings for the same id.
"""
SEEDEOF
seed_note "app/services/id_codec.py"

cat << 'SEEDEOF' > app/services/call_request_service.py
"""
FILE PURPOSE: Business logic for footer call requests (TDR v2 §4.5; PDR v2
§5.13). Framework-agnostic.

PROMPT FOR LLM IMPLEMENTATION:
1. `create_call_request(name, phone, preferred_time=None, message=None)`:
   trim; name 1..150 chars; phone validated via
   auth_service.validate_mobile (E.164-style, country-code-aware) — raise
   InvalidMobileError. Insert with status='new'; organization_id comes from
   the tenant context. Does NOT commit (caller commits).
2. `list_requests(status=None)` newest first.
3. `update_status(call_request, new_status, handler_user)`: legal moves
   new->contacted, contacted->closed, new->closed; otherwise raise
   InvalidCallRequestTransition; set handled_by on first move out of 'new'.
4. FLAGGED: the "Thanks — we'll call you back" acknowledgment (PDR v2 §14.2)
   needs SMS, which arrives in P11 — do NOT send anything now.

DEBUGGING:
# print(f"[DEBUG] call_request created name={name} phone_len={len(phone)}")

OFFLINE DOCKER TEST CASES:
- create_call_request with an invalid phone raises InvalidMobileError and
  inserts nothing.
- closed -> new raises InvalidCallRequestTransition.
"""
SEEDEOF
seed_note "app/services/call_request_service.py"

cat << 'SEEDEOF' >> app/services/auth_service.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend this module):
1. `class InvalidMobileError(Exception)`, `class TermsNotAcceptedError(Exception)`.
2. `validate_mobile(country_code, number) -> (cc, national, e164)` using the
   phonenumbers library; region hint env DEFAULT_MOBILE_REGION; also accepts
   a single full-international string (used by call requests). Invalid ->
   InvalidMobileError.
3. `register_user(...)` gains mobile_country_code, mobile_number,
   accept_terms: accept_terms must be True (else TermsNotAcceptedError); set
   terms_accepted_at=now(UTC). Duplicate-email check is now per-tenant
   (organization scope applies automatically).
4. `complete_account(user, country_code, number, accept_terms)`: validates
   and writes mobile + terms_accepted_at for legacy/staff-created users.
5. `authenticate` and `request_password_reset` are now tenant-scoped by the
   query filter — the same email on two tenants stays two separate accounts.
   The emailed reset link must be built from the CURRENT tenant host
   (url_for(..., _external=True) inside the request), never a global base URL.

DEBUGGING:
# print(f"[DEBUG] register_user org={getattr(g,'organization_id',None)} e164={e164}")

OFFLINE DOCKER TEST CASES:
- register_user with accept_terms=False raises TermsNotAcceptedError, no row.
- The same email registers under org A and org B; a second under org A raises
  DuplicateEmailError.
- A reset request on tenant A's host never creates a token for tenant B's
  same-email user.
"""
SEEDEOF
seed_note "app/services/auth_service.py (P7 addendum)"

cat << 'SEEDEOF' >> app/services/booking_service.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (minimal Postgres lock swap):
1. Replace the SQLite `BEGIN IMMEDIATE` lock in initiate_booking() with a
   normal transaction and `SELECT ... FOR UPDATE` (SQLAlchemy
   `with_for_update()`) on the trek row. Nothing else changes: same checks,
   same exceptions, same return values. (Group bookings / slot_count /
   waitlist holds are P9 — do NOT start them here.)
2. The trek read stays an ORM query so tenant scoping applies automatically.

DEBUGGING:
# print(f"[DEBUG] initiate_booking locked trek_id={trek_id} slots={trek.available_slots}")

OFFLINE DOCKER TEST CASES:
- On Postgres, two concurrent initiate_booking() calls for the last slot
  serialize on the trek row; the existing duplicate/unavailable tests still
  pass unchanged.
- No remaining reference to BEGIN IMMEDIATE anywhere in app/.
"""
SEEDEOF
seed_note "app/services/booking_service.py (P7 addendum)"

cat << 'SEEDEOF' >> app/services/payment_service.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (Postgres + tenancy fixes):
1. In confirm_payment() and cancel_booking(): replace `BEGIN IMMEDIATE` with
   `SELECT ... FOR UPDATE` on the trek row (1:1 swap, no behavior change).
2. confirm_payment() must work WITHOUT a request tenant (webhook): it assumes
   the caller already entered tenant_context(org_id) after deriving the org
   from order id -> payment -> booking -> trek; assert the loaded trek's
   organization_id equals the current org, else raise TenantMismatchError.
3. expire_stale_bookings(): scheduler jobs have no tenant — wrap the body in
   tenant_bypass() (maintenance sweep across all tenants; this is an
   allow-listed bypass user). Behavior otherwise unchanged.

DEBUGGING:
# print(f"[DEBUG] confirm_payment org={getattr(g,'organization_id',None)} order={gateway_order_id}")

OFFLINE DOCKER TEST CASES:
- expire_stale_bookings() run with no tenant context flips stale pending
  bookings for BOTH tenants without raising TenantContextMissingError.
- confirm_payment() under tenant_context(B) for an order belonging to org A
  raises TenantMismatchError and mutates nothing.
"""
SEEDEOF
seed_note "app/services/payment_service.py (P7 addendum)"

cat << 'SEEDEOF' >> app/services/ticket_service.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. purge_expired() runs from APScheduler with no tenant: wrap the delete in
   tenant_bypass() (allow-listed maintenance sweep). Behavior unchanged.

DEBUGGING:
# print(f"[DEBUG] purge_expired deleted={count} (bypass)")

OFFLINE DOCKER TEST CASES:
- purge_expired() with no tenant context deletes expired tickets of both
  tenants and leaves unexpired ones.
"""
SEEDEOF
seed_note "app/services/ticket_service.py (P7 addendum)"

cat << 'SEEDEOF' >> app/services/trek_service.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (shared public/trekker reads):
1. `list_open_treks(difficulty=None, location=None) -> list[Trek]`: status
   'open' AND available_slots > 0, optional filters applied server-side
   (extract the inline query currently in trekker/routes.py so public and
   trekker views share ONE implementation).
2. `get_public_trek(trek_id) -> Trek | None`: returns the trek only when
   status IN ('open','closed','completed'); pending/approved -> None.
3. Both are read-only and tenant-scoped automatically.

DEBUGGING:
# print(f"[DEBUG] list_open_treks difficulty={difficulty} location={location} -> {len(result)}")

OFFLINE DOCKER TEST CASES:
- list_open_treks never returns a trek with available_slots == 0 or a
  non-open status.
- get_public_trek returns None for a 'pending' trek and for another
  tenant's trek id.
"""
SEEDEOF
seed_note "app/services/trek_service.py (P7 addendum)"

cat << 'SEEDEOF' >> app/extensions.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend this module):
1. Define `HashidConverter(werkzeug.routing.BaseConverter)`: `to_python`
   decodes via app.services.id_codec.decode (None -> raise ValidationError so
   Flask returns 404); `to_url` encodes ints via id_codec.encode. Because
   to_url encodes, existing url_for(..., id=<int>) calls in templates need no
   change. Registered as converter name 'hashid' in create_app (see its P7
   addendum). Import-safe and side-effect-free like the rest of this file.

DEBUGGING:
# print(f"[DEBUG] HashidConverter to_python value={value}")

OFFLINE DOCKER TEST CASES:
- url_for('trekker.trek_detail', id=5) yields a path segment that is not '5'
  and round-trips to 5 on request.
- A tampered path segment yields 404, not 500.
"""
SEEDEOF
seed_note "app/extensions.py (P7 addendum)"
log "STEP 6 complete: services seeded"

# -----------------------------------------------------------------------------
# STEP 7: Auth — mobile, consent, account-completion gate
# -----------------------------------------------------------------------------
echo "STEP 7: Seeding auth additions..."

cat << 'SEEDEOF' >> app/auth/forms.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend this module):
1. RegisterForm gains: mobile_country_code (SelectField, choices like
   "+91 (IN)", default from DEFAULT_MOBILE_REGION), mobile_number
   (StringField, DataRequired, digits/spaces only), accept_terms
   (BooleanField, DataRequired — "I accept the Terms and Privacy Policy").
2. New CompleteAccountForm: the same three fields.
3. Server-side validation stays authoritative (auth_service.validate_mobile);
   field-level checks here are only a first filter.

DEBUGGING:
# print(f"[DEBUG] form errors: {form.errors}")

OFFLINE DOCKER TEST CASES:
- RegisterForm.validate() is False when accept_terms is unchecked.
- CompleteAccountForm.validate() is False for an empty mobile_number.
"""
SEEDEOF
seed_note "app/auth/forms.py (P7 addendum)"

cat << 'SEEDEOF' >> app/auth/routes.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend this module):
1. POST /register: pass mobile + accept_terms to auth_service.register_user;
   catch InvalidMobileError / TermsNotAcceptedError -> flash + re-render.
2. Gate order at login (no way to skip any): pending-approval staff ->
   must_reset_password -> needs_account_completion -> dashboard.
3. GET/POST /complete-account (endpoint auth.complete_account):
   @login_required; CompleteAccountForm; on success call
   auth_service.complete_account then redirect by role.
4. Add an `auth_bp.before_app_request` hook: an authenticated user with
   needs_account_completion is redirected to auth.complete_account for every
   request except complete-account, logout and static — so the gate holds for
   already-logged-in sessions too.

DEBUGGING:
# print(f"[DEBUG] completion gate user_id={getattr(current_user,'id',None)} needs={getattr(current_user,'needs_account_completion',None)}")

OFFLINE DOCKER TEST CASES:
- A user with mobile_number='UNSET' logging in lands on /complete-account,
  and GET /trekker/dashboard redirects back to it until completed.
- After a valid submission terms_accepted_at and mobile are stored and the
  dashboard becomes reachable.
"""
SEEDEOF
seed_note "app/auth/routes.py (P7 addendum)"

cat << 'SEEDEOF' >> app/templates/auth/register.html

<!--
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend this template):
1. Add a country-code select + mobile number input (grouped, both labeled,
   required) beneath Email; applies to BOTH roles.
2. Add a required T&C checkbox with links to the tenant's /terms and
   /privacy pages (open in a new tab); the submit stays blocked server-side
   until checked.

DEBUGGING:
DEBUG: register.html mobile+consent fields rendered

OFFLINE DOCKER TEST CASES:
- The rendered form contains mobile_country_code, mobile_number and
  accept_terms inputs, each with a visible <label>.
-->
SEEDEOF
seed_note "app/templates/auth/register.html (P7 addendum)"

cat << 'SEEDEOF' > app/templates/auth/complete_account.html
<!--
FILE PURPOSE: "Complete your account" dead-end screen for legacy or
staff-created users missing a real mobile number and/or T&C acceptance.
NOT in the Blueprint tree — required so NOT NULL mobile + consent are
satisfiable for pre-existing rows.

PROMPT FOR LLM IMPLEMENTATION:
1. No shell (same pattern as forced_password_change.html): centered 400px
   card, message "One more step before you continue".
2. Fields: country code + mobile number, T&C checkbox (links to /terms and
   /privacy), submit. Only other action: Log Out.

DEBUGGING:
DEBUG: complete_account.html rendered for user_id={{ current_user.id }}

OFFLINE DOCKER TEST CASES:
- Rendered output contains no <nav>, sidebar or back link.
- Submitting without ticking the checkbox re-renders with an error.
-->
SEEDEOF
seed_note "app/templates/auth/complete_account.html"
log "STEP 7 complete: auth additions seeded"

# -----------------------------------------------------------------------------
# STEP 8: Public blueprint + templates
# -----------------------------------------------------------------------------
echo "STEP 8: Seeding app/public..."

cat << 'SEEDEOF' > app/public/__init__.py
"""
FILE PURPOSE: Blueprint for anonymous-safe pages: landing, browse, trek
detail, legal text, call-request submit (TDR v2 / Blueprint v2 §1).

PROMPT FOR LLM IMPLEMENTATION:
1. `public_bp = Blueprint("public", __name__,
   template_folder="../templates/trekker")` — NO url_prefix; shares the
   trekker/ templates (no second template tree).
2. Import routes at the bottom: `from app.public import routes`.

DEBUGGING:
# print(f"[DEBUG] public blueprint registered: {public_bp.name}")

OFFLINE DOCKER TEST CASES:
- `from app.public import public_bp` succeeds; public_bp.name == "public".
"""
SEEDEOF
seed_note "app/public/__init__.py"

cat << 'SEEDEOF' > app/public/routes.py
"""
FILE PURPOSE: Public, no-login routes (PDR v2 §5.4, UI/UX v2 §3.1). Same
service calls as trekker/routes.py browse/detail; NO @login_required.

PROMPT FOR LLM IMPLEMENTATION:
1. GET /: authenticated -> redirect to the user's role dashboard; anonymous
   -> landing.html with up to 6 featured open treks and tenant branding.
2. GET /treks: render browse.html using trek_service.list_open_treks with
   optional `difficulty`/`location` params (server-side filtered).
3. GET /treks/<hashid:id>: render trek_detail.html using
   trek_service.get_public_trek; None -> 404.
4. POST /public/call-requests (endpoint public.call_request_submit): CSRF
   protected, `@limiter.limit("5/minute")` (single-instance limiter caveat,
   TDR v2 §15), hidden honeypot field (filled -> silently return the same
   success response, write nothing). Calls
   call_request_service.create_call_request, commits, flashes a thank-you,
   redirects back to a same-host referrer else `/`. Invalid phone -> flash
   error, no row.
5. GET /terms and /privacy: render legal.html with the tenant's
   legal_terms_text / legal_privacy_text; empty text -> a "not yet provided"
   message, never a 500.
6. Expose no tenant-internal data (no user/booking info) on any route here.

DEBUGGING:
# print(f"[DEBUG] public route {request.path} org={g.organization_id}")

OFFLINE DOCKER TEST CASES:
- Anonymous GET /treks returns 200 (no redirect to login).
- A pending trek, and another tenant's trek id, both return 404 on
  /treks/<id>.
- The 6th call-request POST within a minute returns 429; a filled honeypot
  creates no row.
"""
SEEDEOF
seed_note "app/public/routes.py"

cat << 'SEEDEOF' > app/templates/trekker/landing.html
<!--
FILE PURPOSE: Per-tenant public landing page (UI/UX v2 §3.1, UI/UX v1 §5.1).
NOT in the Blueprint tree (placed under trekker/ to avoid a new folder).

PROMPT FOR LLM IMPLEMENTATION:
1. Extend shared/base.html with navbar_trekker.html shell (anonymous mode).
2. Centered hero (max-width 640px): tenant logo/name from `current_org`,
   one-line tagline (contact/branding data only — no invented copy fields),
   "Log In" (outline) + "Register" (solid) buttons.
3. Below: up to 6 featured open-trek cards reusing browse.html's card
   markup, plus a "Browse all treks" link.

DEBUGGING:
DEBUG: landing.html rendered org={{ current_org.name }}

OFFLINE DOCKER TEST CASES:
- Rendered with zero open treks, the page shows an empty-state message,
  not an error.
-->
SEEDEOF
seed_note "app/templates/trekker/landing.html"

cat << 'SEEDEOF' > app/templates/trekker/legal.html
<!--
FILE PURPOSE: Renders a tenant's Terms or Privacy text. NOT in the Blueprint
tree (shared under trekker/).

PROMPT FOR LLM IMPLEMENTATION:
1. Extend base.html; heading + the tenant-supplied legal text (treated as
   plain text, auto-escaped — never |safe), with a "not yet provided"
   fallback when empty.

DEBUGGING:
DEBUG: legal.html rendered kind={{ kind }}

OFFLINE DOCKER TEST CASES:
- Text containing <script> renders escaped, not executed.
-->
SEEDEOF
seed_note "app/templates/trekker/legal.html"

cat << 'SEEDEOF' >> app/templates/trekker/browse.html

<!--
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend this template):
1. Must render for anonymous visitors: never dereference current_user
   attributes without an is_authenticated guard.
2. Each card's "View" links to the hashid public/trekker detail URL.

DEBUGGING:
DEBUG: browse.html anonymous={{ not current_user.is_authenticated }}

OFFLINE DOCKER TEST CASES:
- Anonymous GET /treks renders cards with no Jinja UndefinedError.
-->
SEEDEOF
seed_note "app/templates/trekker/browse.html (P7 addendum)"

cat << 'SEEDEOF' >> app/templates/trekker/trek_detail.html

<!--
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend this template):
1. Anonymous visitor: the booking panel's "Book Now" links to auth.login with
   a `next` param back to this trek; authenticated Trekker keeps today's
   link. Panel stays disabled with explanatory text when not open/no slots.
2. Must render with no current_user attribute access for anonymous users.

DEBUGGING:
DEBUG: trek_detail.html anonymous={{ not current_user.is_authenticated }}

OFFLINE DOCKER TEST CASES:
- Anonymous view shows a Log In link instead of a direct booking link.
-->
SEEDEOF
seed_note "app/templates/trekker/trek_detail.html (P7 addendum)"
log "STEP 8 complete: public blueprint + templates seeded"

# -----------------------------------------------------------------------------
# STEP 9: Shared templates — footer, cookie banner, base, navbar, error page
# -----------------------------------------------------------------------------
echo "STEP 9: Seeding shared templates..."

cat << 'SEEDEOF' > app/templates/shared/_footer.html
<!--
FILE PURPOSE: Sitewide footer (UI/UX v2 §2, §3.1) shown on every shell,
including public pages.

PROMPT FOR LLM IMPLEMENTATION:
1. Copyright line with `current_org.name` and the current year; company
   details from current_org (contact_email, contact_phone — skip blanks).
2. Links to public.terms and public.privacy.
3. "Request a Call" link opening a Bootstrap modal containing a form (name,
   phone, preferred time, message, hidden honeypot, CSRF token) that POSTs to
   public.call_request_submit. Modal is keyboard-navigable; submit result
   announced via the existing ARIA live flash region (UI/UX v2 §8).
4. Pure markup + Bootstrap behavior — no custom JS needed.

DEBUGGING:
DEBUG: _footer.html rendered org={{ current_org.name }}

OFFLINE DOCKER TEST CASES:
- Footer appears on a logged-out page and on an Admin page.
- The call-request form includes a CSRF token and posts to
  public.call_request_submit.
-->
SEEDEOF
seed_note "app/templates/shared/_footer.html"

cat << 'SEEDEOF' > app/templates/shared/_cookie_consent.html
<!--
FILE PURPOSE: Sitewide dismissible cookie-consent banner (UI/UX v2 §3.1).
NOT in the Blueprint tree as its own file (included from base.html).

PROMPT FOR LLM IMPLEMENTATION:
1. Fixed-bottom banner, role="dialog" aria-label="Cookie notice", text
   linking to the tenant's /privacy page, one "Got it" button.
2. Dismissal = a first-party cookie (`tma_cookie_ack`) set by ≤5 lines of
   vanilla JS (display-only UX); the template does not render the banner at
   all when that cookie is present (no flash on load).
3. Keyboard focusable, visible focus ring (UI/UX v2 §8). The timestamped
   legal acceptance still happens at registration/complete-account, not here.

DEBUGGING:
DEBUG: _cookie_consent.html shown={{ show_banner }}

OFFLINE DOCKER TEST CASES:
- With the tma_cookie_ack cookie present the banner markup is absent.
-->
SEEDEOF
seed_note "app/templates/shared/_cookie_consent.html"

cat << 'SEEDEOF' >> app/templates/shared/base.html

<!--
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend base.html):
1. Include shared/_footer.html after {% block content %} on every page.
2. Include shared/_cookie_consent.html just before the closing body tag.
3. Auth-only/dead-end screens (forced_password_change, complete_account,
   pending_approval) override a `show_footer` block/flag to suppress both.

DEBUGGING:
DEBUG: base.html footer+cookie includes active

OFFLINE DOCKER TEST CASES:
- Login, Admin dashboard and public browse all contain the footer markup.
- forced_password_change renders without footer or banner.
-->
SEEDEOF
seed_note "app/templates/shared/base.html (P7 addendum)"

cat << 'SEEDEOF' >> app/templates/shared/navbar_trekker.html

<!--
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend this partial):
1. Serves anonymous visitors too: anonymous -> right side shows "Log In" and
   "Register"; authenticated -> existing avatar dropdown. Brand uses
   current_org.name/logo.
2. "My Bookings"/"Tickets" links render for authenticated Trekkers only;
   "Browse Treks" always.

DEBUGGING:
DEBUG: navbar_trekker.html auth={{ current_user.is_authenticated }}

OFFLINE DOCKER TEST CASES:
- Anonymous render has Log In/Register and no avatar dropdown.
-->
SEEDEOF
seed_note "app/templates/shared/navbar_trekker.html (P7 addendum)"

cat << 'SEEDEOF' >> app/templates/errors/generic.html

<!--
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Accept an optional `message` context variable that overrides the default
   per-code copy (used for the suspended-organization 403).

DEBUGGING:
DEBUG: generic.html code={{ code }} custom_message={{ message is defined }}

OFFLINE DOCKER TEST CASES:
- Rendering code=403 with message="This organization is currently
  unavailable." shows that text, not the default 403 copy.
-->
SEEDEOF
seed_note "app/templates/errors/generic.html (P7 addendum)"
log "STEP 9 complete: shared templates seeded"

# -----------------------------------------------------------------------------
# STEP 10: Admin call-requests queue
# -----------------------------------------------------------------------------
echo "STEP 10: Seeding admin call-requests queue..."

cat << 'SEEDEOF' > app/admin/call_requests_routes.py
"""
FILE PURPOSE: Tenant Admin queue for footer call requests (UI/UX v2 §3.3).
Routes attach to the existing admin_bp.

PROMPT FOR LLM IMPLEMENTATION:
1. GET /admin/call-requests: @login_required + @role_required("admin");
   optional `status` filter; renders call_requests.html.
2. POST /admin/call-requests/<uuid:id>/status: body `status` in
   {contacted, closed}; calls call_request_service.update_status with
   handler_user=current_user; illegal transition -> flash, no write.
3. Use the UUID converter (new table, UUID PK — not hashid).

DEBUGGING:
# print(f"[DEBUG] call_requests list status={request.args.get('status')} count={len(items)}")

OFFLINE DOCKER TEST CASES:
- Staff/Trekker hitting /admin/call-requests gets 403.
- A request from tenant B's host never lists tenant A's call requests.
"""
SEEDEOF
seed_note "app/admin/call_requests_routes.py"

cat << 'SEEDEOF' > app/templates/admin/call_requests.html
<!--
FILE PURPOSE: Call Requests queue table (UI/UX v2 §3.3).

PROMPT FOR LLM IMPLEMENTATION:
1. Admin sidebar shell, `.table-responsive` table: Name, Phone (tel: link),
   Preferred Time, Message (truncated), Status pill, Created, Actions
   ("Mark contacted" / "Close" POST buttons shown only for legal moves).
2. Status filter bar (new / contacted / closed); empty-state message.

DEBUGGING:
DEBUG: call_requests.html count={{ items|length }}

OFFLINE DOCKER TEST CASES:
- A 'closed' row shows no action buttons.
-->
SEEDEOF
seed_note "app/templates/admin/call_requests.html"

cat << 'SEEDEOF' >> app/admin/__init__.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add `from app.admin import call_requests_routes` at the bottom (same
   circular-import-safe pattern as routes).

DEBUGGING:
# print("[DEBUG] admin call_requests_routes imported")

OFFLINE DOCKER TEST CASES:
- create_app().url_map contains admin.call_requests routes.
"""
SEEDEOF
seed_note "app/admin/__init__.py (P7 addendum)"

cat << 'SEEDEOF' >> app/templates/shared/sidebar_admin.html

<!--
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add a "Call Requests" nav link (icon + label) with a small count badge of
   status='new' requests when > 0.

DEBUGGING:
DEBUG: sidebar_admin.html new_call_requests={{ new_call_requests }}

OFFLINE DOCKER TEST CASES:
- Badge is absent when zero new requests exist.
-->
SEEDEOF
seed_note "app/templates/shared/sidebar_admin.html (P7 addendum)"
log "STEP 10 complete: admin call-requests seeded"

# -----------------------------------------------------------------------------
# STEP 11: Opaque IDs across existing route modules + webhook org derivation
# -----------------------------------------------------------------------------
echo "STEP 11: Seeding opaque-ID and webhook addenda..."

for f in app/admin/routes.py app/staff/routes.py app/trekker/routes.py app/tickets/routes.py; do
cat << 'SEEDEOF' >> "$f"

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (opaque identifiers):
1. Replace every `<int:...>` URL converter in this module with the `hashid`
   converter (e.g. `<hashid:id>`). Integer IDs must never appear in a URL.
2. Hidden form fields carrying ids (e.g. StaffActionForm.staff_id) must carry
   the encoded value and be decoded with id_codec.decode on receipt; a None
   result -> 404/400, never a 500.
3. Existing url_for(...) calls keep passing ints — the converter encodes.
4. Unknown/other-tenant ids already 404 via tenant scoping; do not add extra
   existence-leaking messages.

DEBUGGING:
# print(f"[DEBUG] hashid route {request.path}")

OFFLINE DOCKER TEST CASES:
- No rule in this blueprint's url_map still contains an `int` converter.
- GET with a raw integer in place of the encoded id returns 404.
"""
SEEDEOF
seed_note "$f (P7 addendum: hashid)"
done

cat << 'SEEDEOF' >> app/api/__init__.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (opaque identifiers in the API):
1. Change every registered path from `<int:...>` to `<hashid:...>`
   (same app-wide converter).
2. Every integer id in a JSON RESPONSE (`id`, `trek_id`, `booking_id`,
   `user_id`, `staff_id`, `assigned_staff_id`, ...) is encoded with
   id_codec.encode; ids in REQUEST bodies are decoded at the boundary
   (invalid -> 422 VALIDATION_ERROR). UUID ids from new tables stay raw.
3. Provide one shared serializer helper in this package so no resource
   hand-rolls encoding.

DEBUGGING:
# print(f"[DEBUG] api id encode kind={kind} value={value}")

OFFLINE DOCKER TEST CASES:
- GET /api/treks/<id> JSON `id` is a non-numeric opaque string.
- POST /api/bookings with a raw integer trek_id returns 422.
"""
SEEDEOF
seed_note "app/api/__init__.py (P7 addendum)"

cat << 'SEEDEOF' >> app/api/payments_resource.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (webhook has no tenant host):
1. After signature verification, inside tenant_bypass(): look up the Payment
   by gateway_order_id and walk payment -> booking -> trek to obtain exactly
   one organization_id. Unknown order -> log WARNING and return
   `{"received": True}, 200` (stop gateway retries; nothing mutated).
2. Then call confirm_payment() inside `tenant_context(org_id)` — never derive
   the org from request context. Verify the chain resolves to a single org
   before proceeding (TDR v2 §3.3).
3. Signature failure still returns 400 before ANY database access.

DEBUGGING:
# print(f"[DEBUG] webhook order={order_id} derived_org={org_id}")

OFFLINE DOCKER TEST CASES:
- A valid webhook for tenant A's order, posted to a non-tenant host,
  confirms the booking under org A.
- An unknown order id returns 200 and writes nothing.
"""
SEEDEOF
seed_note "app/api/payments_resource.py (P7 addendum)"
log "STEP 11 complete: opaque-ID + webhook addenda seeded"

# -----------------------------------------------------------------------------
# STEP 12: App factory addendum
# -----------------------------------------------------------------------------
echo "STEP 12: Appending P7 addendum to app/__init__.py..."
cat << 'SEEDEOF' >> app/__init__.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend create_app()):
1. Startup checks: fail fast if HASHIDS_SALT is missing; log a WARNING when
   DATABASE_URL is not postgresql (concurrency/isolation suites require
   Postgres). Set engine option pool_pre_ping=True. Never set
   SESSION_COOKIE_DOMAIN (host-only cookies keep tenant sessions separate).
2. Register `app.url_map.converters['hashid'] = HashidConverter` BEFORE any
   blueprint registration.
3. Call `init_tenancy(app)` before registering blueprints.
4. Register public_bp LAST (so '/' never shadows another blueprint).
5. Context processor injecting `current_org` (g.organization.branding(), or
   an empty dict when unset) and a Jinja filter `hid` (id_codec.encode) for
   the rare non-URL display need.
6. CLI `flask seed-tenant-zero`: idempotent; creates the Organization from
   TENANT_ZERO_NAME + a subdomain Domain `<TENANT_ZERO_SUBDOMAIN>.<BASE_DOMAIN>`
   (inside tenant_bypass). Prints the org id and domain.
7. Update `flask seed-admin` (P0): runs inside tenant_context(tenant zero),
   sets mobile from ADMIN_MOBILE_COUNTRY_CODE/NUMBER, leaves
   terms_accepted_at NULL (completion gate forces acceptance), and sets
   organizations.tenant_admin_user_id. Stays idempotent.

DEBUGGING:
# print(f"[DEBUG] create_app v2: db={app.config.get('DATABASE_URL','')[:12]} converters={list(app.url_map.converters)}")

OFFLINE DOCKER TEST CASES:
- create_app() without HASHIDS_SALT raises a clear error.
- `flask seed-tenant-zero` twice yields exactly one organization and one
  domain; `flask seed-admin` twice yields exactly one admin.
- create_app().url_map contains public.index/landing, public.browse,
  public.trek_detail, public.call_request_submit, public.terms,
  public.privacy, auth.complete_account.
"""
SEEDEOF
seed_note "app/__init__.py (P7 addendum)"
log "STEP 12 complete: app factory addendum appended"

# -----------------------------------------------------------------------------
# STEP 13: Tests
# -----------------------------------------------------------------------------
echo "STEP 13: Seeding tests..."

cat << 'SEEDEOF' >> tests/conftest.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (shared multi-tenant fixtures;
this file may already exist — append, don't overwrite):
1. `app` fixture: create_app() against TEST_DATABASE_URL (Postgres), schema
   via db.create_all() or Alembic, dropped after the session.
2. `org_a` / `org_b`: two Organizations, each with one subdomain Domain
   (a.localhost, b.localhost).
3. `client_a` / `client_b`: test clients sending the matching Host header.
4. `ctx_a` / `ctx_b`: tenant_context(org.id) inside an app context for
   service-level tests.
5. `postgres_only` marker: skip when DATABASE_URL is not postgresql.
6. Small factories: make_user(org, role), make_trek(org, ...), with mobile +
   terms fields filled so the completion gate doesn't interfere.

DEBUGGING:
# print(f"[DEBUG] conftest app db={app.config['DATABASE_URL'][:12]}")

OFFLINE DOCKER TEST CASES:
- Two tests in one session don't leak rows into each other (per-test
  rollback/cleanup works).
"""
SEEDEOF
seed_note "tests/conftest.py (P7 addendum)"

cat << 'SEEDEOF' > tests/unit/test_id_codec.py
"""
FILE PURPOSE: Unit tests for services/id_codec.py.

PROMPT FOR LLM IMPLEMENTATION:
1. Round-trip 1..1000; non-numeric encoding; garbage/None -> None; salt
   change -> different output; missing salt -> raises.

DEBUGGING:
# print(f"[DEBUG] id_codec sample encode(1)={encode(1)}")

OFFLINE DOCKER TEST CASES:
- All five behaviors above pass with no Flask app context.
"""
SEEDEOF
seed_note "tests/unit/test_id_codec.py"

cat << 'SEEDEOF' > tests/unit/test_tenant_scoping_mixin.py
"""
FILE PURPOSE: Unit tests for app/tenancy/scoping.py (the structural filter).

PROMPT FOR LLM IMPLEMENTATION:
1. Cover every OFFLINE TEST CASE listed in scoping.py's docstring: filter on
   select, session.get returns None cross-tenant, auto-fill, TenantMismatch
   at flush, fail-closed with no context, bypass + nesting.
2. Mark postgres_only.

DEBUGGING:
# print(f"[DEBUG] scoping test org_a={org_a.id} org_b={org_b.id}")

OFFLINE DOCKER TEST CASES:
- All scoping.py test cases pass on the Postgres test DB.
"""
SEEDEOF
seed_note "tests/unit/test_tenant_scoping_mixin.py"

cat << 'SEEDEOF' > tests/unit/test_call_request_service.py
"""
FILE PURPOSE: Unit tests for services/call_request_service.py.

PROMPT FOR LLM IMPLEMENTATION:
1. Valid create; invalid phone rejected with no row; legal/illegal status
   transitions; handled_by set on first move out of 'new'.

DEBUGGING:
# print(f"[DEBUG] call_request test id={req.id}")

OFFLINE DOCKER TEST CASES:
- closed -> new raises InvalidCallRequestTransition.
"""
SEEDEOF
seed_note "tests/unit/test_call_request_service.py"

cat << 'SEEDEOF' > tests/integration/test_tenancy_resolver.py
"""
FILE PURPOSE: Integration tests for app/tenancy/resolver.py.

PROMPT FOR LLM IMPLEMENTATION:
1. Cover every OFFLINE TEST CASE in resolver.py's docstring using client_a /
   client_b Host headers: unknown host 404, unverified custom 404, suspended
   403, port stripping, platform host skip, webhook path exempt.
2. A session cookie issued on host A is not accepted on host B (user_loader
   returns nothing under org B).

DEBUGGING:
# print(f"[DEBUG] resolver test host={host} status={resp.status_code}")

OFFLINE DOCKER TEST CASES:
- All six resolver behaviors plus the cross-host session case pass.
"""
SEEDEOF
seed_note "tests/integration/test_tenancy_resolver.py"

cat << 'SEEDEOF' > tests/integration/test_public_routes.py
"""
FILE PURPOSE: Integration tests for app/public/routes.py and the
account-completion gate.

PROMPT FOR LLM IMPLEMENTATION:
1. Anonymous /, /treks, /treks/<id> render; pending trek and other-tenant
   trek -> 404; call-request create/rate-limit/honeypot; /terms and
   /privacy with and without text.
2. Legacy user (mobile 'UNSET') is gated to /complete-account until valid.
3. Footer and cookie banner appear on public pages.

DEBUGGING:
# print(f"[DEBUG] public test path={path} status={resp.status_code}")

OFFLINE DOCKER TEST CASES:
- Every case above passes with zero live network calls.
"""
SEEDEOF
seed_note "tests/integration/test_public_routes.py"

cat << 'SEEDEOF' >> tests/integration/test_booking_race.py

"""
P7 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (port to Postgres):
1. Run the existing last-slot race against the Postgres TEST_DATABASE_URL
   (the file-backed-SQLite requirement no longer applies). Two threads,
   separate sessions/connections, threading.Barrier start.
2. Assertions unchanged: exactly one 'booked', the other cancelled/refunded,
   available_slots ends at 0. Group-booking variant is P9.
3. Add a guard test: grep-style assertion that `BEGIN IMMEDIATE` appears
   nowhere under app/.

DEBUGGING:
# print(f"[DEBUG] pg race final_slots={trek.available_slots}")

OFFLINE DOCKER TEST CASES:
- Passes deterministically across 20 consecutive runs on Postgres.
"""
SEEDEOF
seed_note "tests/integration/test_booking_race.py (P7 addendum)"
log "STEP 13 complete: tests seeded"

# -----------------------------------------------------------------------------
# STEP 14: Commits (left for after code-implementer fills in real logic)
# -----------------------------------------------------------------------------
#git add docker-compose.yml requirements.txt .env.example docs/P7_POSTGRES_REBASELINE.md
#git commit -m "feat(db): migrate schema to Postgres"
#git add app/tenancy app/models
#git commit -m "feat(tenancy): organization/domain model and request-level resolution"
#git add app/public app/templates/trekker app/services/trek_service.py
#git commit -m "feat(public): anonymous browse and trek detail routes"
#git add app/services/id_codec.py app/extensions.py
#git commit -m "feat(security): opaque resource identifiers"
#git add app/auth app/services/auth_service.py app/templates/shared app/services/call_request_service.py app/admin
#git commit -m "feat(compliance): mandatory mobile number, cookie consent, footer"
#git push -u origin "$BRANCH_NAME"

# -----------------------------------------------------------------------------
# STEP 15: Summary
# -----------------------------------------------------------------------------
echo ""
echo "====================================================="
echo "Milestone P7 (v2) Seeded Successfully!"
echo "-----------------------------------------------------"
echo "Seed files created this run:"
for f in "${SEED_FILES[@]}"; do echo "  - $f"; done
echo "-----------------------------------------------------"
echo "NEXT STEPS:"
echo "1. Copy new keys from .env.example into .env."
echo "2. Feed these seeds to the code-implementer skill (start with"
echo "   app/tenancy/scoping.py, then models, then the rest)."
echo "3. Follow docs/P7_POSTGRES_REBASELINE.md, then:"
echo "   docker compose run --rm test   (Postgres-backed; must pass)"
echo "4. Open a PR: $BRANCH_NAME -> main once tests pass."
echo "5. Execute P8.sh (Identity & Access Governance)."
echo "====================================================="
log "===== P7.sh (v2) completed successfully ====="

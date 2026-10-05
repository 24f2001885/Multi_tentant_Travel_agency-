#!/usr/bin/env bash
###############################################################################
# P8.sh — v2 MILESTONE 8: Identity & Access Governance
# TMA Platform — Multi-Tenant Trekking Management SaaS
#
# Covers (Master Project Blueprint v2 §3, phase P8):
#   - Platform Admin control plane (own auth, own table, own blueprint,
#     tenant onboarding, cross-tenant audit viewer + meta-audit trail)
#   - Dynamic roles & capabilities (role builder, protected capabilities,
#     capability-driven navigation)
#   - tests/isolation/ cross-tenant suite
#
# Depends on: P7 (tenancy resolver/scoping, organizations/domains, hashid).
# Never writes real application logic — only git/dir/config setup + PROMPT
# seeds for the code-implementer skill.
#
# FLAGGED DECISIONS:
#  1. PERMISSION TIMING: PDR v2 §5.3 says permission changes apply at the
#     staff member's NEXT LOGIN; TDR v2 §6 describes a per-request role lookup
#     (live). Seeded per the PDR: capabilities are snapshotted into the
#     session at login. Trade-off: a revoked capability stays usable until
#     logout/session expiry (≤12h). Confirm if live revocation is preferred.
#  2. CAPABILITY SCOPE: admin-side screens (users, trek CRUD, payments,
#     reports…) stay Tenant-Admin-only (@role_required("admin")). Capabilities
#     gate the staff-side screens and the shared tickets screens. Later phases
#     append their own keys (P9 bookings.*, P10 treks.*, P11 campaigns.*).
#  3. LEGACY STAFF: v1 staff have role_id NULL after this phase and would
#     lose all access. `flask seed-rbac` backfills approved staff with a
#     "Trek Coordinator" role holding every current staff capability
#     (behavior-preserving), audit-logged.
#  4. organizations.created_by_platform_admin_id is tightened to FK + NOT NULL
#     here (loosened in P7). Run `flask seed-platform-admin` and backfill
#     tenant zero's value BEFORE applying that migration.
#  5. Platform dashboard: Route-revenue and infra-load panels are labeled
#     empty states until P9 / P13 supply the data.
#  6. Custom-domain verification is manual (Platform Admin clicks "Mark
#     verified" after checking DNS). Automated DNS checks are undecided.
#  7. The temp password returned at tenant onboarding is shown ONCE and never
#     logged or stored in plaintext.
#  8. platform_admins has no mobile column (TDR v2 §4.1) although PDR v2 §5.5
#     says every role needs one — left as in the TDR; confirm.
#  9. Isolation gate: .github/workflows/test.yml only arrives in P14; until
#     then the gate is the local `docker compose run --rm test`, which already
#     collects tests/isolation/.
# 10. New env vars beyond TDR v2 §16: PLATFORM_ADMIN_NAME/EMAIL/PASSWORD.
#
# USAGE:
#   cd ~/Desktop/t
#   bash ./docs/P8.sh
###############################################################################

set -euo pipefail

BRANCH_NAME="phase/P8-identity-access-governance"
LOG_FILE="./docs/p8_v2_milestone.log"

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
log "===== P8.sh (v2) started — Identity & Access Governance ====="

# -----------------------------------------------------------------------------
# STEP 0: Pre-flight
# -----------------------------------------------------------------------------
echo "STEP 0: Pre-flight checks..."
[ -d ".git" ] || { echo "ERROR: no .git found."; exit 1; }
for f in app/tenancy/scoping.py app/tenancy/resolver.py app/models/organization.py \
         app/models/domain.py app/services/id_codec.py app/public/routes.py; do
  [ -f "$f" ] || { echo "ERROR: $f missing — run P7.sh first."; exit 1; }
done
log "STEP 0 complete: pre-flight checks passed"

# -----------------------------------------------------------------------------
# STEP 1: Branch
# -----------------------------------------------------------------------------
echo "STEP 1: Checking out milestone branch..."
checkout_phase_branch "phase/P7-platform-foundation" "$BRANCH_NAME"
log "STEP 1 complete: on $BRANCH_NAME (base: phase/P7-platform-foundation)"

# -----------------------------------------------------------------------------
# STEP 2: Directories + env
# -----------------------------------------------------------------------------
echo "STEP 2: Creating directories..."
mkdir -p app/platform app/templates/platform tests/isolation tests/unit tests/integration
log "STEP 2 complete: app/platform, templates/platform, tests/isolation created"

if ! grep -q "TMA Platform v2 (P8)" .env.example; then
cat << 'SEEDEOF' >> .env.example

# --- TMA Platform v2 (P8) ---
PLATFORM_ADMIN_NAME=Platform Owner
PLATFORM_ADMIN_EMAIL=owner@platform.local
PLATFORM_ADMIN_PASSWORD=change-me-platform-password
SEEDEOF
fi
seed_note ".env.example"
echo "  !! Copy the new keys from .env.example into your real .env."

# -----------------------------------------------------------------------------
# STEP 3: Models
# -----------------------------------------------------------------------------
echo "STEP 3: Seeding models..."

cat << 'SEEDEOF' > app/models/platform_admin.py
"""
FILE PURPOSE: The `platform_admins` table — the platform operator's own
account, structurally outside every tenant (TDR v2 §4.1). NOT tenant-scoped,
no FK into any tenant table.

PROMPT FOR LLM IMPLEMENTATION:
1. Columns: id (UUID PK), name (String(120) NOT NULL), email (String(255)
   NOT NULL UNIQUE, lower-cased), password_hash (String(255) NOT NULL),
   is_active (Boolean NOT NULL default True), created_at
   (DateTime(timezone=True) NOT NULL default now).
2. set_password/check_password via werkzeug (same scrypt default as users).
3. Do NOT inherit flask_login.UserMixin and never expose get_id — this model
   must be impossible for login_manager.user_loader to load.

DEBUGGING:
# print(f"[DEBUG] platform_admin {self.id} active={self.is_active}")

OFFLINE DOCKER TEST CASES:
- Duplicate email raises IntegrityError.
- check_password round-trips; a PlatformAdmin cannot be returned by the
  tenant user_loader.
"""
SEEDEOF
seed_note "app/models/platform_admin.py"

cat << 'SEEDEOF' > app/models/platform_access_log.py
"""
FILE PURPOSE: The `platform_access_logs` meta-audit trail — who at platform
level read which tenant's data, and when (TDR v2 §4.6). Separate from
audit_logs on purpose. NOT tenant-scoped (organization_id here names the
tenant whose data was accessed; it is a plain column, not the mixin).

PROMPT FOR LLM IMPLEMENTATION:
1. Columns: id (UUID PK), platform_admin_id (UUID FK platform_admins.id NOT
   NULL), organization_id (UUID FK organizations.id NOT NULL), action
   (String(100) NOT NULL, e.g. 'viewed_audit_log'), created_at
   (DateTime(timezone=True) NOT NULL default now).
2. Classmethod `record(platform_admin_id, organization_id, action)` builds
   and returns the row WITHOUT committing — the caller writes it in the SAME
   transaction as the cross-tenant read it logs (TDR v2 §12).
3. Append-only: expose no update/delete helpers.

DEBUGGING:
# print(f"[DEBUG] platform_access {self.platform_admin_id} -> org {self.organization_id} {self.action}")

OFFLINE DOCKER TEST CASES:
- record() returns an unsaved instance with created_at populated at flush.
- If the surrounding transaction rolls back, no log row persists.
"""
SEEDEOF
seed_note "app/models/platform_access_log.py"

cat << 'SEEDEOF' > app/models/capability.py
"""
FILE PURPOSE: The global `capabilities` menu (TDR v2 §4.2). NOT tenant-scoped.

PROMPT FOR LLM IMPLEMENTATION:
1. Columns: id (UUID PK), key (String(100) NOT NULL UNIQUE, e.g.
   'tickets.respond'), description (String(255) NOT NULL), is_protected
   (Boolean NOT NULL default False — listing/documentation only; enforcement
   NEVER reads this flag, TDR v2 §6).
2. Rows are managed only by rbac_service.seed_catalog (idempotent upsert).

DEBUGGING:
# print(f"[DEBUG] capability {self.key} protected={self.is_protected}")

OFFLINE DOCKER TEST CASES:
- Duplicate key raises IntegrityError.
"""
SEEDEOF
seed_note "app/models/capability.py"

cat << 'SEEDEOF' > app/models/role.py
"""
FILE PURPOSE: Tenant-defined `roles` and the `role_capabilities` join table
(TDR v2 §4.2).

PROMPT FOR LLM IMPLEMENTATION:
1. Role inherits TenantScopedMixin. Columns: id (UUID PK), name (String(100)
   NOT NULL), is_starter_template (Boolean NOT NULL default False),
   created_at (DateTime(timezone=True) NOT NULL default now).
   UniqueConstraint(organization_id, name).
2. `role_capabilities` association table: role_id (UUID FK roles.id), 
   capability_id (UUID FK capabilities.id), composite PK. No organization_id
   (association table; scoped through role). Relationship Role.capabilities.
3. Application layer (rbac_service) must reject any write of a protected
   capability key into this table — the model does not rely on convention.

DEBUGGING:
# print(f"[DEBUG] role {self.name} org={self.organization_id} caps={len(self.capabilities)}")

OFFLINE DOCKER TEST CASES:
- Same role name in two orgs is allowed; twice in one org raises
  IntegrityError.
- Deleting a role removes its role_capabilities rows.
"""
SEEDEOF
seed_note "app/models/role.py"

cat << 'SEEDEOF' >> app/models/staff_profile.py

"""
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (modify this model):
1. Add role_id (UUID FK roles.id, NULLABLE until a role is assigned) and
   relationship `role`.
2. Property `capability_keys` is NOT cached on the model (the login-time
   session snapshot is the source of truth for request checks).

DEBUGGING:
# print(f"[DEBUG] staff_profile {self.id} role_id={self.role_id}")

OFFLINE DOCKER TEST CASES:
- A StaffProfile may hold role_id=None; assigning a role from another
  organization is rejected by rbac_service (not by this model).
"""
SEEDEOF
seed_note "app/models/staff_profile.py (P8 addendum)"

cat << 'SEEDEOF' >> app/models/organization.py

"""
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (tighten created_by):
1. Add the FK created_by_platform_admin_id -> platform_admins.id and make it
   NOT NULL. Migration order: seed a platform admin, backfill tenant zero,
   THEN add the FK + NOT NULL (see FLAGGED DECISION 4 in P8.sh).
2. Relationship `tenant_admin` -> User via tenant_admin_user_id (read-only,
   no cascade).

DEBUGGING:
# print(f"[DEBUG] org {self.id} created_by={self.created_by_platform_admin_id}")

OFFLINE DOCKER TEST CASES:
- Inserting an Organization with no created_by_platform_admin_id raises
  IntegrityError after this migration.
"""
SEEDEOF
seed_note "app/models/organization.py (P8 addendum)"

cat << 'SEEDEOF' >> app/models/__init__.py

"""
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Import and add to __all__: PlatformAdmin, PlatformAccessLog, Capability,
   Role (and the role_capabilities table object).

DEBUGGING:
# print(f"[DEBUG] models package exports {len(__all__)}")

OFFLINE DOCKER TEST CASES:
- db.metadata.tables now also contains platform_admins,
  platform_access_logs, capabilities, roles, role_capabilities.
"""
SEEDEOF
seed_note "app/models/__init__.py (P8 addendum)"
log "STEP 3 complete: models seeded"

# -----------------------------------------------------------------------------
# STEP 4: Services
# -----------------------------------------------------------------------------
echo "STEP 4: Seeding services..."

cat << 'SEEDEOF' > app/services/rbac_service.py
"""
FILE PURPOSE: Role/capability logic + the protected-capability list
(TDR v2 §4.2, §6; PDR v2 §5.3, §11). Framework-agnostic.

PROMPT FOR LLM IMPLEMENTATION:
1. Module constant PROTECTED_CAPABILITY_KEYS (code-level, NOT read from the
   DB): 'settings.org.edit', 'payments.payout.configure',
   'manual_booking.toggle', 'manual_booking.sign_off', 'roles.manage',
   'org.delete'.
2. Module constant CAPABILITY_CATALOG (single source for the menu), each
   (key, description): 'treks.manage_assigned', 'participants.view',
   'payments.view_assigned', 'tickets.view', 'tickets.respond',
   'reports.view' — plus the six protected keys flagged is_protected for
   listing only. Later phases APPEND keys; never rename/remove.
3. `seed_catalog()`: idempotent upsert of the catalog; never deletes rows.
4. `seed_starter_roles(org)`: creates is_starter_template roles —
   "Finance" (payments.view_assigned, reports.view), "Support" (tickets.view,
   tickets.respond, participants.view), "Trek Coordinator" (all six
   non-protected keys above). Idempotent per (org, name).
5. `create_role / update_role / delete_role(actor, org, ...)`: reject any
   key in PROTECTED_CAPABILITY_KEYS with ProtectedCapabilityError (checked
   against the CODE constant, not is_protected); unknown key -> UnknownCapability;
   delete rejected while any StaffProfile holds the role. Every create/edit/
   delete AND every capability grant/revoke diff is written via
   AuditLog.record (actions role.create, role.update, role.delete,
   role.capability_grant, role.capability_revoke). No commit — caller commits.
6. `assign_role(actor, staff_profile, role)`: role must belong to the same org;
   AuditLog 'role.assign'.
7. `get_capability_keys(user) -> list[str]`: for staff, keys of their role
   (empty if none); used ONCE at login for the session snapshot.
8. `backfill_staff_roles()`: for every org, approved staff with role_id NULL
   get "Trek Coordinator" (audit details 'legacy backfill'). Idempotent.

DEBUGGING:
# print(f"[DEBUG] rbac role={role.name} keys={sorted(keys)}")

OFFLINE DOCKER TEST CASES:
- create_role including 'manual_booking.toggle' raises
  ProtectedCapabilityError and writes nothing.
- Even if a capability row's is_protected flag is manually set False in the
  DB, a PROTECTED key is still rejected (code constant wins).
- update_role that adds/removes keys writes one audit row per grant/revoke.
- assign_role with a role from another org raises and changes nothing.
- backfill_staff_roles twice assigns once.
"""
SEEDEOF
seed_note "app/services/rbac_service.py"

cat << 'SEEDEOF' > app/services/tenancy_service.py
"""
FILE PURPOSE: Organization/domain provisioning used by Platform Admin routes
and the `create-tenant` CLI (TDR v2 §5, §13). Runs under tenant_bypass()
(allow-listed). Framework-agnostic.

PROMPT FOR LLM IMPLEMENTATION:
1. `create_organization(name, subdomain, tenant_admin_email, tenant_admin_name,
   platform_admin) -> (org, admin_user, temp_password)` in ONE transaction:
   validate subdomain (DNS-safe lowercase, 3–63 chars, not in a reserved list:
   www, platform, api, admin, mail, static), unique domain `<sub>.<BASE_DOMAIN>`;
   create Organization (created_by_platform_admin_id), the subdomain Domain,
   the first Tenant Admin User (role='admin', random secrets-based temp
   password, must_reset_password=True, legacy-sentinel mobile + NULL
   terms_accepted_at so the completion gate fires), set
   org.tenant_admin_user_id, call rbac_service.seed_starter_roles(org).
   The temp password is returned once and never logged.
2. `suspend_organization / reactivate_organization(org, platform_admin)`.
3. `add_custom_domain(org, domain_name)` (domain_type='custom',
   verified_at NULL, normalized + unique) and
   `mark_domain_verified(domain, platform_admin)`.
4. `list_organizations_with_counts()`: per org user/booking counts
   (read-only, bypass).
5. `read_tenant_audit_log(platform_admin, org, page, per_page)`: writes a
   PlatformAccessLog row FIRST in the same transaction; if that write fails
   the read is aborted (never an unlogged cross-tenant read). Then returns
   the page of that org's audit_logs, newest first.

DEBUGGING:
# print(f"[DEBUG] tenancy_service org={org.id} action={action}")

OFFLINE DOCKER TEST CASES:
- create_organization with a reserved or duplicate subdomain raises and
  creates no rows (full rollback, including the User).
- read_tenant_audit_log leaves exactly one PlatformAccessLog row per call;
  with the log insert forced to fail, no audit data is returned.
- The temp password hash verifies but the plaintext appears in no log line.
"""
SEEDEOF
seed_note "app/services/tenancy_service.py"
log "STEP 4 complete: services seeded"

# -----------------------------------------------------------------------------
# STEP 5: Platform blueprint
# -----------------------------------------------------------------------------
echo "STEP 5: Seeding app/platform..."

cat << 'SEEDEOF' > app/platform/__init__.py
"""
FILE PURPOSE: Blueprint for the Platform Admin control plane — structurally
separate from every tenant blueprint (TDR v2 §2). Import as `app.platform`
only.

PROMPT FOR LLM IMPLEMENTATION:
1. `platform_bp = Blueprint("platform", __name__,
   template_folder="../templates/platform", url_prefix="/platform")`.
2. Import routes at the bottom: `from app.platform import routes`.
3. No tenant decorators anywhere in this package; only
   @platform_admin_required (app/platform/auth.py).

DEBUGGING:
# print(f"[DEBUG] platform blueprint registered: {platform_bp.name}")

OFFLINE DOCKER TEST CASES:
- `from app.platform import platform_bp` succeeds; the stdlib `platform`
  module still imports normally elsewhere in the app.
"""
SEEDEOF
seed_note "app/platform/__init__.py"

cat << 'SEEDEOF' > app/platform/auth.py
"""
FILE PURPOSE: Platform Admin's own login path and session — entirely separate
from Flask-Login tenant auth (TDR v2 §2, §6).

PROMPT FOR LLM IMPLEMENTATION:
1. `platform_login(email, password) -> PlatformAdmin | None` (generic failure,
   is_active required); on success store ONLY `session["platform_admin_id"]`
   (never call flask_login.login_user for a platform admin).
2. `platform_logout()`, `current_platform_admin()` (returns None when absent).
3. `platform_admin_required` decorator: redirect to platform.login if no valid
   session; NEVER stacks with any tenant-aware decorator.
4. Routes in app/platform/routes.py: GET/POST /platform/login with
   `@limiter.limit("5/minute")`, POST /platform/logout.
5. A platform session is meaningless on a tenant host and a tenant session is
   meaningless on the platform host (host separation + resolver path guard).

DEBUGGING:
# print(f"[DEBUG] platform auth admin_id={session.get('platform_admin_id')}")

OFFLINE DOCKER TEST CASES:
- Wrong password and unknown email return the identical generic error.
- After platform login, GET any /admin/* tenant route on a tenant host is
  still unauthenticated.
- The 6th login attempt within a minute returns 429.
"""
SEEDEOF
seed_note "app/platform/auth.py"

cat << 'SEEDEOF' > app/platform/forms.py
"""
FILE PURPOSE: WTForms for the Platform Admin console.

PROMPT FOR LLM IMPLEMENTATION:
1. PlatformLoginForm: email, password.
2. OnboardOrganizationForm: name (≤150), subdomain (regex ^[a-z0-9-]{3,63}$),
   tenant_admin_name, tenant_admin_email.
3. CustomDomainForm: domain_name (hostname regex, lower-cased).
4. PlatformProfileForm: name; PlatformChangePasswordForm: current, new
   (min 8), confirm.

DEBUGGING:
# print(f"[DEBUG] form errors: {form.errors}")

OFFLINE DOCKER TEST CASES:
- OnboardOrganizationForm rejects subdomain "Bad_Name" and "ab".
"""
SEEDEOF
seed_note "app/platform/forms.py"

cat << 'SEEDEOF' > app/platform/routes.py
"""
FILE PURPOSE: Platform Admin console routes (UI/UX v2 §3.2). Every route
except login uses @platform_admin_required. Runs under tenant_bypass().

PROMPT FOR LLM IMPLEMENTATION:
1. GET/POST /platform/login, POST /platform/logout (see auth.py).
2. GET /platform/dashboard: organization counts by status + per-tenant
   user/booking counts via tenancy_service.list_organizations_with_counts.
   FLAGGED: "net Route revenue" and "infrastructure load" panels render
   labeled empty states until P9 / P13.
3. GET /platform/organizations: table — name, primary domain, status badge,
   Route-linkage badge, user/booking counts.
4. GET/POST /platform/organizations/new: OnboardOrganizationForm ->
   tenancy_service.create_organization; the result page shows the temp
   password ONCE (never flashed to session, never logged).
5. GET /platform/organizations/<uuid:id>: detail — branding preview, domains
   with verification badge, Route-linkage badge (read-only until P9), tenant
   admin, suspend/reactivate buttons.
6. POST .../suspend, .../reactivate; POST .../domains (add custom domain);
   POST /platform/domains/<uuid:id>/verify (manual "Mark verified").
7. GET /platform/organizations/<uuid:id>/audit-logs: via
   tenancy_service.read_tenant_audit_log; the template states this view is
   itself logged.
8. GET/POST /platform/profile: edit name + change password.
9. Backups console and /platform/stats are P13 — do NOT seed them.

DEBUGGING:
# print(f"[DEBUG] platform route {request.path} admin={session.get('platform_admin_id')}")

OFFLINE DOCKER TEST CASES:
- Every route except login redirects to login without a platform session.
- Opening the audit viewer adds one PlatformAccessLog row per page view.
- Suspending an org makes its tenant host return 403 on the next request.
"""
SEEDEOF
seed_note "app/platform/routes.py"
log "STEP 5 complete: app/platform seeded"

# -----------------------------------------------------------------------------
# STEP 6: Decorators + RBAC wiring on existing modules
# -----------------------------------------------------------------------------
echo "STEP 6: Seeding RBAC wiring..."

cat << 'SEEDEOF' >> app/auth/decorators.py

"""
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (append to this module):
1. `requires_capability(key)`: stack beneath @login_required. Tenant Admin
   (current_user.id == g.organization.tenant_admin_user_id) passes any
   NON-protected key; a staff user passes only if `key` is in
   session["capability_keys"] (login-time snapshot); anyone else abort(403).
   Unknown keys in code raise at import/decoration time, not at request time.
2. `protected_capability_required(view)`: passes ONLY when
   `current_user.id == g.organization.tenant_admin_user_id`; otherwise
   abort(403). It must NOT query roles, role_capabilities or capabilities —
   structurally unable to be granted by the role builder (TDR v2 §6).
3. Both compose with @role_required and @approved_staff_required per the
   P1/P3 convention.

DEBUGGING:
# print(f"[DEBUG] capability check user={current_user.id} key={key} snapshot={session.get('capability_keys')}")

OFFLINE DOCKER TEST CASES:
- A staff user whose role lacks 'tickets.respond' gets 403 on a route
  requiring it; the Tenant Admin passes.
- A staff user whose role was edited AFTER login still has the old snapshot
  until next login.
- A @protected_capability_required route returns 403 for every staff user
  regardless of role contents, and for a second 'admin'-role user who is not
  org.tenant_admin_user_id.
"""
SEEDEOF
seed_note "app/auth/decorators.py (P8 addendum)"

cat << 'SEEDEOF' >> app/auth/routes.py

"""
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend this module):
1. After a successful login, store
   session["capability_keys"] = rbac_service.get_capability_keys(user) for
   staff users (empty list otherwise); clear it on logout.
2. Platform Admin never passes through this module.

DEBUGGING:
# print(f"[DEBUG] login snapshot user={user.id} keys={session.get('capability_keys')}")

OFFLINE DOCKER TEST CASES:
- Logging in as staff with role X stores exactly X's capability keys.
- Logout removes capability_keys from the session.
"""
SEEDEOF
seed_note "app/auth/routes.py (P8 addendum)"

cat << 'SEEDEOF' >> app/staff/routes.py

"""
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (capability gating):
1. Add @requires_capability beneath the existing decorators: manage /
   update-slots / transition -> 'treks.manage_assigned'; participants ->
   'participants.view'. @staff_owns_trek still applies (ownership AND
   capability are both required).
2. Participants table: the Payment Status column renders only with
   'payments.view_assigned'.
3. Dashboard: each stat card/chart renders only if its capability is held
   (assigned treks card -> treks.manage_assigned; participants card ->
   participants.view; payment summary -> payments.view_assigned; chart ->
   reports.view). With none held show a "No permissions assigned yet —
   contact your administrator" empty state (absent, not disabled).

DEBUGGING:
# print(f"[DEBUG] staff route {request.path} caps={session.get('capability_keys')}")

OFFLINE DOCKER TEST CASES:
- Staff with only 'tickets.view' gets 403 on /staff/treks/<id>/manage.
- Staff with participants.view but not payments.view_assigned sees the
  roster without the Payment Status column.
"""
SEEDEOF
seed_note "app/staff/routes.py (P8 addendum)"

cat << 'SEEDEOF' >> app/tickets/routes.py

"""
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (capability gating, staff only):
1. For role='staff': list/detail require 'tickets.view'; POST respond
   requires 'tickets.respond'. Existing scoping (Problem tickets on assigned
   treks only) still applies on top. Admin and Trekker behavior unchanged.

DEBUGGING:
# print(f"[DEBUG] tickets cap check role={current_user.role} caps={session.get('capability_keys')}")

OFFLINE DOCKER TEST CASES:
- Staff with 'tickets.view' but not 'tickets.respond' can open a ticket but
  gets 403 posting a response.
"""
SEEDEOF
seed_note "app/tickets/routes.py (P8 addendum)"

cat << 'SEEDEOF' >> app/tenancy/resolver.py

"""
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (platform boundary guard):
1. On PLATFORM_ADMIN_HOST allow only /platform/* (and static); anything else
   -> 404. On tenant hosts, /platform/* -> 404. The two planes never serve
   each other's routes.

DEBUGGING:
# print(f"[DEBUG] boundary host={host} path={request.path}")

OFFLINE DOCKER TEST CASES:
- GET /admin/dashboard on the platform host -> 404.
- GET /platform/login on a tenant host -> 404.
"""
SEEDEOF
seed_note "app/tenancy/resolver.py (P8 addendum)"

cat << 'SEEDEOF' >> app/admin/routes.py

"""
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (staff approval + role assignment):
1. POST /admin/staff/<hashid:id>/approve now ALSO requires a `role_id` (an
   existing role of this org); approval and rbac_service.assign_role run in
   one transaction. Approving without a role is rejected with a flash.
2. Add the role-assignment endpoint to roles_routes.py (see that file); this
   module only passes the roles list to staff_list/staff_detail templates.

DEBUGGING:
# print(f"[DEBUG] staff approve staff_id={id} role_id={role_id}")

OFFLINE DOCKER TEST CASES:
- Approving staff with a role id from another organization is rejected and
  leaves approval_status 'pending'.
"""
SEEDEOF
seed_note "app/admin/routes.py (P8 addendum)"

cat << 'SEEDEOF' >> app/admin/__init__.py

"""
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add `from app.admin import roles_routes` at the bottom.

DEBUGGING:
# print("[DEBUG] admin roles_routes imported")

OFFLINE DOCKER TEST CASES:
- create_app().url_map contains admin.roles_list, admin.role_new,
  admin.role_edit, admin.role_delete, admin.staff_assign_role.
"""
SEEDEOF
seed_note "app/admin/__init__.py (P8 addendum)"

cat << 'SEEDEOF' > app/admin/roles_routes.py
"""
FILE PURPOSE: Tenant Admin "Roles & Capabilities" management and staff role
assignment (UI/UX v2 §3.3; PDR v2 §14.1 /admin/roles, /admin/staff/<id>/role).
Role management is itself a PROTECTED capability.

PROMPT FOR LLM IMPLEMENTATION:
1. Every route: @login_required + @role_required("admin") +
   @protected_capability_required.
2. GET /admin/roles (list: name, capability count, staff count, starter
   flag); GET/POST /admin/roles/new; GET/POST /admin/roles/<hashid:...>/edit
   — wait: roles use UUID PKs, so use the `uuid` converter, not hashid;
   POST /admin/roles/<uuid:id>/delete (rejected while staff hold it).
3. POST /admin/staff/<hashid:id>/role (endpoint admin.staff_assign_role):
   body `role_id`; calls rbac_service.assign_role.
4. The capability checklist lists ONLY non-protected capabilities — protected
   keys are absent from the form entirely (never rendered disabled). A
   smuggled protected key in the POST body -> 422/flash from
   ProtectedCapabilityError, nothing written.
5. Changes are audit-logged by rbac_service; flash a note that staff see
   permission changes at their next login.

DEBUGGING:
# print(f"[DEBUG] roles route {request.path} user={current_user.id}")

OFFLINE DOCKER TEST CASES:
- A staff user (any role) hitting /admin/roles gets 403.
- POSTing a role form with 'roles.manage' in the capability list is rejected
  and writes nothing.
- The rendered checklist HTML contains none of the six protected keys.
"""
SEEDEOF
seed_note "app/admin/roles_routes.py"
log "STEP 6 complete: RBAC wiring seeded"

# -----------------------------------------------------------------------------
# STEP 7: Templates
# -----------------------------------------------------------------------------
echo "STEP 7: Seeding templates..."

cat << 'SEEDEOF' > app/templates/shared/sidebar_platform.html
<!--
FILE PURPOSE: Platform Admin shell (UI/UX v2 §1 Principle 7, §2, §7) — a
distinct shell, NOT sidebar_admin.html relabeled.

PROMPT FOR LLM IMPLEMENTATION:
1. Same responsive behavior as sidebar_admin (240px / icon-only / off-canvas)
   but its own CSS tokens: --platform-sidebar-bg #0B1120, --platform-accent
   #7C3AED, --platform-accent-soft #F1E9FE (UI/UX v2 §7).
2. Links: Dashboard, Organizations, Cross-Tenant Audit (via org detail),
   Profile; "Platform" wordmark treatment unlike tenant branding. P13 later
   adds Backups and Monitoring links. No tenant switcher.
3. Bottom: platform admin name + Log Out (POST, CSRF).

DEBUGGING:
DEBUG: sidebar_platform.html active={{ request.endpoint }}

OFFLINE DOCKER TEST CASES:
- Rendered output uses the platform tokens and none of the tenant sidebar
  classes/colors.
- Logout is a POST form.
-->
SEEDEOF
seed_note "app/templates/shared/sidebar_platform.html"

cat << 'SEEDEOF' > app/templates/platform/login.html
<!--
FILE PURPOSE: Platform Admin login (no tenant branding, no footer call form).

PROMPT FOR LLM IMPLEMENTATION:
1. Centered 400px card, platform wordmark, email + password, generic error
   copy; no "Register" and no "Forgot password" link.

DEBUGGING:
DEBUG: platform/login.html rendered

OFFLINE DOCKER TEST CASES:
- Form posts to platform.login with a CSRF token.
-->
SEEDEOF
seed_note "app/templates/platform/login.html"

cat << 'SEEDEOF' > app/templates/platform/dashboard.html
<!--
FILE PURPOSE: Platform Dashboard (UI/UX v2 §3.2).

PROMPT FOR LLM IMPLEMENTATION:
1. sidebar_platform shell. Stat cards: total organizations, active,
   suspended, total bookings (all tenants).
2. Per-tenant table: name, users, bookings, status badge.
3. Two panels — "Net Route revenue" and "Infrastructure load" — rendered as
   clearly labeled "Available after P9 / P13" empty states.

DEBUGGING:
DEBUG: platform/dashboard.html orgs={{ orgs|length }}

OFFLINE DOCKER TEST CASES:
- Numerals come from context variables, never hardcoded.
-->
SEEDEOF
seed_note "app/templates/platform/dashboard.html"

cat << 'SEEDEOF' > app/templates/platform/organizations_list.html
<!--
FILE PURPOSE: Organizations list (UI/UX v2 §3.2).

PROMPT FOR LLM IMPLEMENTATION:
1. Table (.table-responsive): Name, Primary Domain, Status badge
   (organization status), Route Linkage badge, Users, Bookings, Actions
   (View). Primary button "+ Onboard Organization" top-right (platform
   accent).

DEBUGGING:
DEBUG: organizations_list.html count={{ orgs|length }}

OFFLINE DOCKER TEST CASES:
- A suspended org shows the danger status badge.
-->
SEEDEOF
seed_note "app/templates/platform/organizations_list.html"

cat << 'SEEDEOF' > app/templates/platform/onboard_organization.html
<!--
FILE PURPOSE: Onboard New Organization form + one-time result view.

PROMPT FOR LLM IMPLEMENTATION:
1. Form: org name, subdomain (with live ".<BASE_DOMAIN>" suffix hint),
   Tenant Admin name + email.
2. Result state (same template): shows the generated temp password once with
   an explicit "will not be shown again" notice; no copy persisted anywhere.

DEBUGGING:
DEBUG: onboard_organization.html state={{ 'result' if temp_password else 'form' }}

OFFLINE DOCKER TEST CASES:
- Reloading the result page does not re-display the password.
-->
SEEDEOF
seed_note "app/templates/platform/onboard_organization.html"

cat << 'SEEDEOF' > app/templates/platform/organization_detail.html
<!--
FILE PURPOSE: Organization Detail (UI/UX v2 §3.2).

PROMPT FOR LLM IMPLEMENTATION:
1. Key/value card: name, status badge, tenant admin, GST number (read-only),
   Route linkage badge (read-only until P9), branding preview (logo, two
   color swatches, contact details).
2. Domains table: domain, type, verification badge (pending/verified),
   "Mark verified" for custom domains; add-custom-domain form.
3. Suspend / Reactivate button (confirm step) and a "View audit log" link
   noting that viewing is itself logged.

DEBUGGING:
DEBUG: organization_detail.html org={{ org.id }}

OFFLINE DOCKER TEST CASES:
- "Mark verified" appears only for unverified custom domains.
-->
SEEDEOF
seed_note "app/templates/platform/organization_detail.html"

cat << 'SEEDEOF' > app/templates/platform/audit_viewer.html
<!--
FILE PURPOSE: Cross-Tenant Audit Log viewer (UI/UX v2 §3.2).

PROMPT FOR LLM IMPLEMENTATION:
1. Banner at top: "Viewing this log is recorded in the platform access log."
2. Same table as admin/audit_log.html (timestamp, actor, action, target,
   expandable JSON details), paginated newest-first.

DEBUGGING:
DEBUG: audit_viewer.html org={{ org.id }} page={{ page }}

OFFLINE DOCKER TEST CASES:
- The notice banner is always present.
-->
SEEDEOF
seed_note "app/templates/platform/audit_viewer.html"

cat << 'SEEDEOF' > app/templates/platform/profile.html
<!--
FILE PURPOSE: Platform Admin profile/settings.

PROMPT FOR LLM IMPLEMENTATION:
1. Centered card inside sidebar_platform: name (editable), email (read-only),
   Change Password sub-form (current, new, confirm).

DEBUGGING:
DEBUG: platform/profile.html rendered

OFFLINE DOCKER TEST CASES:
- Wrong current password rejects the change and leaves the hash unchanged.
-->
SEEDEOF
seed_note "app/templates/platform/profile.html"

cat << 'SEEDEOF' > app/templates/admin/roles_list.html
<!--
FILE PURPOSE: Roles & Capabilities list (UI/UX v2 §3.3).

PROMPT FOR LLM IMPLEMENTATION:
1. Admin sidebar shell; table: Name, Capabilities (count), Staff (count),
   Starter badge, Actions (Edit, Delete — Delete disabled with tooltip while
   staff hold the role). Primary button "+ New Role".

DEBUGGING:
DEBUG: roles_list.html count={{ roles|length }}

OFFLINE DOCKER TEST CASES:
- Delete is disabled (not hidden) for a role with assigned staff.
-->
SEEDEOF
seed_note "app/templates/admin/roles_list.html"

cat << 'SEEDEOF' > app/templates/admin/role_form.html
<!--
FILE PURPOSE: Role create/edit form (UI/UX v2 §3.3).

PROMPT FOR LLM IMPLEMENTATION:
1. Role name + grouped capability checklist (group by key prefix: treks,
   participants, payments, tickets, reports). Only NON-protected capabilities
   appear; protected ones are absent, never disabled.
2. Note under the checklist: "Changes apply to staff at their next login."

DEBUGGING:
DEBUG: role_form.html mode={{ 'edit' if role else 'create' }}

OFFLINE DOCKER TEST CASES:
- Rendered HTML contains no input for any protected capability key.
-->
SEEDEOF
seed_note "app/templates/admin/role_form.html"

cat << 'SEEDEOF' >> app/templates/shared/sidebar_admin.html

<!--
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add a "Roles & Capabilities" link (Tenant Admin sees every link; only the
   tenant admin ever renders this shell).

DEBUGGING:
DEBUG: sidebar_admin.html roles link rendered

OFFLINE DOCKER TEST CASES:
- The Roles link is active-styled on admin.roles_list.
-->
SEEDEOF
seed_note "app/templates/shared/sidebar_admin.html (P8 addendum)"

cat << 'SEEDEOF' >> app/templates/shared/sidebar_staff.html

<!--
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (capability-driven nav):
1. Every nav link (Dashboard always; Participants/Manage -> treks.manage_assigned
   or participants.view; Tickets -> tickets.view) is rendered only when
   `has_capability(key)` is true. Absent, not disabled. No hardcoded
   "if role == staff" branches (UI/UX v2 Principle 6).
2. `has_capability` is a Jinja global reading the login-time snapshot.

DEBUGGING:
DEBUG: sidebar_staff.html caps={{ session.get('capability_keys') }}

OFFLINE DOCKER TEST CASES:
- Staff with only 'tickets.view' sees Dashboard + Tickets and nothing else.
-->
SEEDEOF
seed_note "app/templates/shared/sidebar_staff.html (P8 addendum)"

cat << 'SEEDEOF' >> app/templates/shared/_badge_macros.html

<!--
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (new badge domains, UI/UX v2 §6):
1. Add domains: 'organization_status' (active #16A34A/#E7F6EC, suspended
   #DC2626/#FBE9E9), 'route_linkage' (pending #D97706/#FDF3E3, verified
   #16A34A/#E7F6EC), 'domain_verification' (pending #D97706/#FDF3E3,
   verified #16A34A/#E7F6EC). Same badge spec and neutral fallback as before.

DEBUGGING:
DEBUG: status_badge domain={{ domain }} value={{ value }}

OFFLINE DOCKER TEST CASES:
- status_badge('suspended','organization_status') renders #DC2626.
-->
SEEDEOF
seed_note "app/templates/shared/_badge_macros.html (P8 addendum)"

cat << 'SEEDEOF' >> app/templates/admin/staff_list.html

<!--
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add a "Role" column (role name or "—"). On pending rows the Approve
   action includes a required role select (this org's roles only).

DEBUGGING:
DEBUG: staff_list.html roles={{ roles|length }}

OFFLINE DOCKER TEST CASES:
- Approve cannot be submitted without choosing a role.
-->
SEEDEOF
seed_note "app/templates/admin/staff_list.html (P8 addendum)"

cat << 'SEEDEOF' >> app/templates/admin/staff_detail.html

<!--
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Profile card shows the assigned role and a "Change role" select posting to
   admin.staff_assign_role; note "applies at next login".

DEBUGGING:
DEBUG: staff_detail.html role={{ staff.role_id }}

OFFLINE DOCKER TEST CASES:
- Changing role posts a hashid staff id and a UUID role id.
-->
SEEDEOF
seed_note "app/templates/admin/staff_detail.html (P8 addendum)"

cat << 'SEEDEOF' >> app/templates/staff/dashboard.html

<!--
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Wrap each stat card/chart in its capability check (see staff/routes.py
   addendum) and render the "No permissions assigned yet" empty state when
   none apply.

DEBUGGING:
DEBUG: staff/dashboard.html caps={{ session.get('capability_keys') }}

OFFLINE DOCKER TEST CASES:
- Staff with no role sees only the empty state.
-->
SEEDEOF
seed_note "app/templates/staff/dashboard.html (P8 addendum)"
log "STEP 7 complete: templates seeded"

# -----------------------------------------------------------------------------
# STEP 8: App factory addendum
# -----------------------------------------------------------------------------
echo "STEP 8: Appending P8 addendum to app/__init__.py..."
cat << 'SEEDEOF' >> app/__init__.py

"""
P8 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend create_app()):
1. Register platform_bp (after tenancy init; the resolver already skips
   tenant resolution for PLATFORM_ADMIN_HOST).
2. Register Jinja global `has_capability(key)`: reads
   session["capability_keys"]; Tenant Admin -> True for non-protected keys.
3. CLI `flask seed-platform-admin`: idempotent, from PLATFORM_ADMIN_NAME/
   EMAIL/PASSWORD (fail clearly if unset).
4. CLI `flask create-tenant --name --subdomain --admin-email [--admin-name]`:
   calls tenancy_service.create_organization (needs an existing platform
   admin as creator), prints the temp password once.
5. CLI `flask seed-rbac`: rbac_service.seed_catalog(), then for every org
   seed_starter_roles + backfill_staff_roles (all inside tenant_bypass).
6. Ensure the tenant user_loader only ever resolves tenant User ids.

DEBUGGING:
# print(f"[DEBUG] create_app P8: blueprints={list(app.blueprints)}")

OFFLINE DOCKER TEST CASES:
- `flask seed-platform-admin` twice -> exactly one platform admin.
- `flask seed-rbac` twice -> no duplicate capabilities/roles/assignments.
- create_app().url_map contains platform.login, platform.dashboard,
  platform.organizations_list, platform.organization_new.
"""
SEEDEOF
seed_note "app/__init__.py (P8 addendum)"
log "STEP 8 complete: app factory addendum appended"

# -----------------------------------------------------------------------------
# STEP 9: Tests
# -----------------------------------------------------------------------------
echo "STEP 9: Seeding tests..."

cat << 'SEEDEOF' > tests/isolation/test_tenant_scoping.py
"""
FILE PURPOSE: The dedicated cross-tenant data-isolation suite (TDR v2 §10) —
the highest-priority test file of the platform. Required gate from P8 onward.

PROMPT FOR LLM IMPLEMENTATION:
1. Auto-discover every TenantScopedMixin subclass (via the mapper registry).
   A registry of per-model factories creates one row under org A; the test
   FAILS if any discovered model has no factory (so a newly added tenant
   model cannot silently skip isolation coverage).
2. For each model: org-B session cannot list it, cannot db.session.get it,
   cannot update/delete it.
3. HTTP layer: for every route with an id parameter, requesting org A's id on
   org B's host returns 404; admin/staff/trekker/tickets/public/api alike.
4. Allow-list test: a source-scan asserting tenant_bypass is imported only by
   the approved modules (resolver, platform/*, tenancy_service, rbac_service
   CLI helpers, payments_resource, payment_service/ticket_service jobs,
   app/__init__ CLI).
5. Platform session on a tenant host and tenant session on the platform host
   both yield no access.

DEBUGGING:
# print(f"[DEBUG] isolation checking model={model.__name__}")

OFFLINE DOCKER TEST CASES:
- Adding a new TenantScopedMixin model without a factory makes this suite
  fail loudly.
- Every discovered model passes read/get/update/delete isolation.
"""
SEEDEOF
seed_note "tests/isolation/test_tenant_scoping.py"

cat << 'SEEDEOF' > tests/integration/test_platform_admin_auth.py
"""
FILE PURPOSE: Platform Admin auth + console integration tests.

PROMPT FOR LLM IMPLEMENTATION:
1. Cover every OFFLINE TEST CASE in app/platform/auth.py and routes.py:
   generic login failure, rate limit, route protection, separate session,
   audit-viewer meta-log, suspend -> 403 on tenant host, one-time temp
   password display.

DEBUGGING:
# print(f"[DEBUG] platform test path={path} status={resp.status_code}")

OFFLINE DOCKER TEST CASES:
- All listed behaviors pass with no live network.
"""
SEEDEOF
seed_note "tests/integration/test_platform_admin_auth.py"

cat << 'SEEDEOF' > tests/integration/test_protected_capabilities.py
"""
FILE PURPOSE: Protected-capability tests (TDR v2 §10).

PROMPT FOR LLM IMPLEMENTATION:
1. Generate every plausible role/capability combination through the
   role-builder service AND routes (including smuggled protected keys, direct
   role_capabilities inserts, is_protected flipped to False); assert no
   non-owner ever passes a @protected_capability_required route.
2. A second user with role 'admin' who is not tenant_admin_user_id gets 403.

DEBUGGING:
# print(f"[DEBUG] protected test combo={combo} status={resp.status_code}")

OFFLINE DOCKER TEST CASES:
- Zero combinations grant protected access to anyone but the tenant admin.
"""
SEEDEOF
seed_note "tests/integration/test_protected_capabilities.py"

cat << 'SEEDEOF' > tests/integration/test_rbac_roles_routes.py
"""
FILE PURPOSE: Integration tests for roles routes + capability-driven nav.

PROMPT FOR LLM IMPLEMENTATION:
1. Create/edit/delete role via routes; delete blocked while assigned;
   assignment requires same-org role; approve-with-role; nav rendering shows
   only held links; snapshot-at-login semantics (edit role after login ->
   old snapshot until next login).

DEBUGGING:
# print(f"[DEBUG] roles test role={role_name}")

OFFLINE DOCKER TEST CASES:
- All listed behaviors pass.
"""
SEEDEOF
seed_note "tests/integration/test_rbac_roles_routes.py"

cat << 'SEEDEOF' > tests/unit/test_rbac_service.py
"""
FILE PURPOSE: Unit tests for services/rbac_service.py.

PROMPT FOR LLM IMPLEMENTATION:
1. Cover every OFFLINE TEST CASE in rbac_service.py's docstring, incl.
   audit rows per grant/revoke and idempotent seeding/backfill.

DEBUGGING:
# print(f"[DEBUG] rbac unit role={role.name}")

OFFLINE DOCKER TEST CASES:
- Code-constant protection holds even with is_protected flipped in the DB.
"""
SEEDEOF
seed_note "tests/unit/test_rbac_service.py"

cat << 'SEEDEOF' > tests/unit/test_tenancy_service.py
"""
FILE PURPOSE: Unit tests for services/tenancy_service.py.

PROMPT FOR LLM IMPLEMENTATION:
1. Cover every OFFLINE TEST CASE in tenancy_service.py's docstring: atomic
   onboarding rollback, reserved/duplicate subdomains, meta-log-first audit
   reads, temp password never logged.

DEBUGGING:
# print(f"[DEBUG] tenancy unit org={org.id}")

OFFLINE DOCKER TEST CASES:
- Failed onboarding leaves zero rows in organizations, domains, users, roles.
"""
SEEDEOF
seed_note "tests/unit/test_tenancy_service.py"
log "STEP 9 complete: tests seeded"

# -----------------------------------------------------------------------------
# STEP 10: Commits (left for after code-implementer fills in real logic)
# -----------------------------------------------------------------------------
#git add app/platform app/models app/services/tenancy_service.py app/templates/platform app/templates/shared/sidebar_platform.html
#git commit -m "feat(platform): platform admin console and tenant onboarding"
#git add app/services/rbac_service.py app/admin/roles_routes.py app/templates/admin
#git commit -m "feat(rbac): dynamic role and capability model"
#git add app/auth app/staff app/tickets
#git commit -m "feat(security): protected-capability enforcement"
#git add tests/isolation
#git commit -m "test(isolation): cross-tenant data access suite"
#git push -u origin "$BRANCH_NAME"

# -----------------------------------------------------------------------------
# STEP 11: Summary
# -----------------------------------------------------------------------------
echo ""
echo "====================================================="
echo "Milestone P8 (v2) Seeded Successfully!"
echo "-----------------------------------------------------"
echo "Seed files created this run:"
for f in "${SEED_FILES[@]}"; do echo "  - $f"; done
echo "-----------------------------------------------------"
echo "NEXT STEPS:"
echo "1. Copy new keys from .env.example into .env."
echo "2. Feed these seeds to the code-implementer skill."
echo "3. flask seed-platform-admin && flask seed-rbac, then backfill"
echo "   tenant zero's created_by_platform_admin_id (see FLAGGED DECISION 4)."
echo "4. docker compose run --rm test   (isolation suite must pass)"
echo "5. Open a PR: $BRANCH_NAME -> main once tests pass."
echo "6. Execute P9.sh (Booking & Money Engine v2)."
echo "====================================================="
log "===== P8.sh (v2) completed successfully ====="

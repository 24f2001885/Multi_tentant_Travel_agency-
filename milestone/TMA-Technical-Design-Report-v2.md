TECHNICAL DESIGN REPORT v2: TMA Platform — Multi-Tenant Trekking Management SaaS

*Companion to Project Definition Report v2. Assumes the v1 TDR's delivered design (P0–P5, plus P6's API Integration and Security Hardening) as the working foundation. This document specifies only what changes or is newly added for the multi-tenant platform; any table, endpoint, or mechanism from TDR v1 not mentioned here is assumed unchanged except for the organization-scoping retrofit described in §4. Where TDR v1 content is superseded outright (the locking strategy, the role/permission model), that supersession is stated explicitly rather than left implicit.*

---

## 1. Introduction & Conventions

**Runtime:** Python 3.11, unchanged from v1.

**Database:** PostgreSQL 15+, replacing SQLite. This is not a drop-in swap — SQLite's `BEGIN IMMEDIATE` single-writer locking, which the original booking engine depended on for concurrency safety, has no direct Postgres equivalent and is re-derived in §3.

**Naming conventions:** unchanged from v1 — `snake_case` for variables/functions/columns/files, `PascalCase` for classes, plural `snake_case` for tables, resource-based plural-noun REST paths.

**New convention for this phase — tenant scoping:** every model representing tenant-owned data inherits a shared `TenantScopedMixin` providing an `organization_id` column and a SQLAlchemy-level default query filter (see §6). No new tenant-scoped model may be added to the codebase without this mixin; this is a hard code-review gate given the data-leakage risk it closes.

**New convention — opaque identifiers:** every new table introduced in this phase uses a UUID primary key rather than an auto-incrementing integer. Existing v1 tables keep their integer primary keys internally (avoiding a disruptive migration on live data and foreign-key chains), but every URL and API response exposes a reversible, non-sequential encoded form of the ID (see §6) rather than the raw integer.

---

## 2. System Architecture

**Shape:** still a single Flask monolith (application-factory pattern, unchanged), now serving multiple tenants from one running backend rather than one dedicated deployment per client. A monolith remains the right call here — the complexity this phase adds is in data isolation and permissioning, not in a need for independently-scalable services.

**Tenant resolution (new, request-level):** a `before_request` hook inspects `request.host`. It is matched against the `domains` table (every row mapping a subdomain or verified custom domain to an `organization_id`). On a match, `g.organization_id` is set for the duration of the request; on no match, the request is rejected with 404 before any route logic runs. Every subsequent query in that request must be scoped by `g.organization_id` via the `TenantScopedMixin` (§6) — there is no code path that should ever need to bypass this except Platform Admin's own control-plane routes, which run under a completely separate blueprint with no tenant context at all.

**Platform Admin isolation:** Platform Admin does not authenticate through the tenant-aware auth system at all. It has its own login path, its own session, and its own `platform_admins` table (§4) with zero foreign-key relationship into any tenant's data — the only way Platform Admin ever touches tenant data is through explicit, logged, read-only cross-tenant queries in its own control-plane routes (e.g. the audit-log read described in §6), never through impersonating a tenant session.

**Request flow (representative path — a confirmed group booking with split payment):**
1. Browser → tenant's own frontend → `POST /api/bookings` against the shared backend, resolved via `g.organization_id` from the incoming subdomain.
2. Route validates the trek belongs to the resolved organization (defense in depth beyond the tenant-scoped query itself).
3. `booking_service.initiate_booking()` now accepts a participant count/list, creates one `bookings` row with `slot_count = N` and `N` `booking_participants` rows, re-checks `available_slots >= slot_count` under lock.
4. `payment_service.create_gateway_order()` creates the Razorpay order as before, now additionally attaching the tenant's linked Route account so the eventual capture auto-splits.
5. Razorpay webhook → `payment_service.confirm_payment()` — re-derived locking (§3), decrements `available_slots` by `slot_count`, and on success triggers the Route transfer to the tenant's account.
6. On any slot released anywhere in the system (cancellation, confirmation-time loser path, stale-booking expiry), a `waitlist_service` check runs to see if any `waitlist_entries` can now be offered the freed slot(s).

**Caching, rate limiting, job locking (deferred):** all three remain in-process for this phase, with the same single-instance limitation already true of v1, now explicitly called out as a blocker to running more than one app instance (§15). Not solved in this phase.

---

## 3. Flagship Feature: The Multi-Tenant, Concurrency-Safe Booking & Split-Payment Engine

This supersedes TDR v1 §3 outright. The original engine solved one hard problem — never double-selling a slot under concurrent requests. This phase's version must solve the same problem under a different database's locking model, while additionally handling a variable number of slots per booking (group bookings), a tenant boundary that must never leak, and a payment settlement that must land in the correct tenant's account every time.

### 3.1 Re-deriving the lock: SQLite `BEGIN IMMEDIATE` → Postgres `SELECT ... FOR UPDATE`

SQLite's `BEGIN IMMEDIATE` works by taking a database-wide write lock the instant a transaction starts, which is crude but sufficient for a single-writer file database. Postgres supports real row-level locking, which is both more precise and the correct replacement: every place the original design opened `BEGIN IMMEDIATE` and re-read the `treks` row, the Postgres version instead issues `SELECT * FROM treks WHERE id = :id FOR UPDATE` inside an explicit transaction. This locks only the specific trek row being booked — not the whole database — so bookings on *different* treks no longer serialize against each other at all, a genuine improvement in multi-tenant throughput over the SQLite-era design, while two requests racing for the *same* trek's last slot(s) still serialize exactly as intended.

The two re-check points from the original design (initiation-time and confirmation-time) are preserved unchanged in principle; only the locking primitive underneath them changes.

### 3.2 Group bookings: slot arithmetic generalizes, the race does not

A booking now carries a `slot_count` (≥1) rather than implicitly always consuming exactly one slot. Every comparison and mutation that previously read `available_slots >= 1` / `available_slots -= 1` is generalized to `available_slots >= booking.slot_count` / `available_slots -= booking.slot_count`. The locking discipline is unchanged — the `FOR UPDATE` row lock still makes this check-then-decrement atomic regardless of how many slots a single booking claims. The concurrency test suite (§10) must be extended with a case where two group bookings together exceed remaining capacity but neither alone would, proving the generalized check still rejects correctly.

### 3.3 Tenant boundary inside the engine

Every row the engine touches — `treks`, `bookings`, `payments` — is organization-scoped. The engine itself does not need special tenant-aware logic beyond what `TenantScopedMixin` already enforces at the query layer (§6), but the webhook handler is a deliberate exception worth flagging: Razorpay's webhook has no tenant context of its own (no subdomain to resolve from), so `payment_service.confirm_payment()` must derive the organization strictly from the `gateway_order_id` → `payments` → `bookings` → `treks` chain already stored at order-creation time, never from request context, and must verify that chain resolves to exactly one organization before proceeding.

### 3.4 Split settlement

On the winner path of `confirm_payment()`, once the slot decrement and status updates commit, a Razorpay Route transfer is initiated to the trek's organization's linked payout account for the booking's amount, net of whatever platform commission is eventually decided (see §15 — this percentage/flat-fee is not yet defined and is a blocking prerequisite before this step can be implemented, not an engineering unknown). The transfer's own status (pending/processed/failed) is tracked on the `payments` row; a failed transfer must not roll back the already-confirmed booking — the slot is the customer's regardless of a downstream settlement hiccup, which instead surfaces to Platform Admin's financial-reconciliation view for manual follow-up.

### 3.5 Waitlist promotion as a consequence of this engine, not a separate one

Any point where this engine increments `available_slots` back up — a cancellation (self or forced), a confirmation-time loser, or the stale-booking expiry sweep — must, in the same transaction or immediately after, check `waitlist_entries` for that trek and, if any are waiting, create a time-limited offer for the earliest entries up to the number of slots freed. This keeps slot-count mutation a single, traceable concern rather than scattering "did a slot just free up?" checks across every caller.

---

## 4. Database Design

Tables are grouped by whether they are **new**, **modified** (adding fields/relationships to a v1 table), or **unchanged** (listed for completeness only, full definition in TDR v1).

### 4.1 New — Platform & Tenancy

- `platform_admins`
  - `id` (UUID, Primary Key)
  - `name` (VARCHAR(120), NOT NULL)
  - `email` (VARCHAR(255), NOT NULL, UNIQUE)
  - `password_hash` (VARCHAR(255), NOT NULL)
  - `is_active` (BOOLEAN, NOT NULL, DEFAULT TRUE)
  - `created_at` (TIMESTAMPTZ, NOT NULL, DEFAULT now())
  - *Deliberately has no foreign key into any tenant table — structurally outside the tenant boundary, per PDR v2 §4.*

- `organizations`
  - `id` (UUID, Primary Key)
  - `name` (VARCHAR(150), NOT NULL)
  - `status` (VARCHAR(20), NOT NULL, DEFAULT `'active'`, CHECK `IN ('active','suspended')`)
  - `logo_url` (VARCHAR(500), NULLABLE)
  - `primary_color` / `secondary_color` (VARCHAR(7), NULLABLE — hex codes)
  - `contact_email` / `contact_phone` (VARCHAR(255) / VARCHAR(20), NULLABLE)
  - `legal_terms_text` / `legal_privacy_text` (TEXT, NULLABLE — owned by the tenant's legal team)
  - `gst_number` (VARCHAR(20), NULLABLE — required before invoicing is enabled for that org)
  - `razorpay_route_account_id` (VARCHAR(100), NULLABLE — set once Route linkage/KYC completes)
  - `tenant_admin_user_id` (INTEGER, Foreign Key → `users.id`, NULLABLE until the first Admin is created)
  - `created_by_platform_admin_id` (UUID, Foreign Key → `platform_admins.id`, NOT NULL)
  - `created_at` (TIMESTAMPTZ, NOT NULL, DEFAULT now())
  - *`tenant_admin_user_id` is the direct-ownership field every protected-capability check (§6) tests against — not a role lookup.*

- `domains`
  - `id` (UUID, Primary Key)
  - `organization_id` (UUID, Foreign Key → `organizations.id`, NOT NULL)
  - `domain_name` (VARCHAR(255), NOT NULL, UNIQUE)
  - `domain_type` (VARCHAR(20), NOT NULL, CHECK `IN ('subdomain','custom')`)
  - `is_primary` (BOOLEAN, NOT NULL, DEFAULT TRUE)
  - `verified_at` (TIMESTAMPTZ, NULLABLE — custom domains require DNS verification before routing is enabled)

### 4.2 New — Dynamic Roles & Permissions

- `capabilities`
  - `id` (UUID, Primary Key)
  - `key` (VARCHAR(100), NOT NULL, UNIQUE — e.g. `'tickets.respond'`, `'treks.edit'`)
  - `description` (VARCHAR(255), NOT NULL)
  - `is_protected` (BOOLEAN, NOT NULL, DEFAULT FALSE — protected rows exist for documentation/listing purposes only; enforcement never reads this flag, per §6)

- `roles`
  - `id` (UUID, Primary Key)
  - `organization_id` (UUID, Foreign Key → `organizations.id`, NOT NULL)
  - `name` (VARCHAR(100), NOT NULL)
  - `is_starter_template` (BOOLEAN, NOT NULL, DEFAULT FALSE — seeds like "Finance" / "Support" offered at tenant creation, fully editable afterward)
  - `created_at` (TIMESTAMPTZ, NOT NULL, DEFAULT now())
  - UNIQUE (`organization_id`, `name`)

- `role_capabilities`
  - `role_id` (UUID, Foreign Key → `roles.id`, NOT NULL)
  - `capability_id` (UUID, Foreign Key → `capabilities.id`, NOT NULL)
  - PRIMARY KEY (`role_id`, `capability_id`)
  - *A protected capability must never appear here — enforced at the application layer on every write to this table, not just by convention.*

### 4.3 Modified — Core v1 Tables

- `users`, `staff_profiles`, `treks`, `bookings`, `payments`, `tickets`, `password_reset_tokens`, `audit_logs` — each gains:
  - `organization_id` (UUID, Foreign Key → `organizations.id`, NOT NULL) via `TenantScopedMixin`.
- `staff_profiles` additionally gains:
  - `role_id` (UUID, Foreign Key → `roles.id`, NULLABLE until a role is assigned)
- `users` additionally gains:
  - `mobile_number` (VARCHAR(20), NOT NULL)
  - `mobile_country_code` (VARCHAR(5), NOT NULL)
  - `terms_accepted_at` (TIMESTAMPTZ, NOT NULL)
- `bookings` additionally gains:
  - `slot_count` (INTEGER, NOT NULL, DEFAULT 1, CHECK `slot_count > 0`)
  - `booking_channel` (VARCHAR(10), NOT NULL, DEFAULT `'online'`, CHECK `IN ('online','manual')`)
  - `manual_sign_off_by` (INTEGER, Foreign Key → `users.id`, NULLABLE)
  - `manual_sign_off_at` (TIMESTAMPTZ, NULLABLE)
  - `cancellation_reason` (VARCHAR(30), NULLABLE, CHECK `IN ('customer_requested','staff_force_cancel','payment_failed','identity_verification_failed')` — the last value reserved for the still-parked ID-verification feature)
  - `cancelled_by` (INTEGER, Foreign Key → `users.id`, NULLABLE)
  - `refund_percentage_applied` (NUMERIC(5,2), NULLABLE)
  - `coupon_id` (UUID, Foreign Key → `coupons.id`, NULLABLE)
- `payments` additionally gains:
  - `channel` (VARCHAR(10), NOT NULL, DEFAULT `'razorpay'`, CHECK `IN ('razorpay','offline')` — `gateway_order_id` becomes NULLABLE, required only when `channel='razorpay'`)
  - `route_transfer_id` (VARCHAR(100), NULLABLE)
  - `route_transfer_status` (VARCHAR(20), NULLABLE, CHECK `IN ('pending','processed','failed')`)
- `tickets` keeps its existing `response`/`responded_by_id`/`responded_at` columns for backward compatibility with already-closed v1 tickets, but new activity goes through `ticket_messages` (§4.4) — the Detail view reads both, preferring the message thread when present.

### 4.4 New — Business Features

- `refund_policies`
  - `id` (UUID, Primary Key)
  - `organization_id` (UUID, Foreign Key → `organizations.id`, NOT NULL)
  - `min_days_before_trip` (INTEGER, NOT NULL)
  - `max_days_before_trip` (INTEGER, NULLABLE — NULL means "and above")
  - `refund_percentage` (NUMERIC(5,2), NOT NULL, CHECK `BETWEEN 0 AND 100`)
  - *Any day-count not covered by a row resolves to 0% at calculation time (PDR v2 §5.9) — not a DB constraint, a service-layer default.*

- `coupons`
  - `id` (UUID, Primary Key)
  - `organization_id` (UUID, Foreign Key, NOT NULL)
  - `code` (VARCHAR(30), NOT NULL)
  - `discount_type` (VARCHAR(10), NOT NULL, CHECK `IN ('percentage','flat')`)
  - `discount_value` (NUMERIC(10,2), NOT NULL)
  - `valid_from` / `valid_until` (DATE, NULLABLE)
  - `max_uses` (INTEGER, NULLABLE)
  - `used_count` (INTEGER, NOT NULL, DEFAULT 0)
  - `min_booking_amount` (NUMERIC(10,2), NULLABLE)
  - `is_active` (BOOLEAN, NOT NULL, DEFAULT TRUE)
  - UNIQUE (`organization_id`, `code`)

- `coupon_redemptions`
  - `id` (UUID, Primary Key), `coupon_id` (FK), `booking_id` (FK), `user_id` (FK), `discount_applied` (NUMERIC(10,2)), `created_at`

- `invoices`
  - `id` (UUID, Primary Key), `booking_id` (FK, UNIQUE), `organization_id` (FK)
  - `invoice_number` (VARCHAR(50), NOT NULL — sequential per organization, not globally)
  - `taxable_amount` / `gst_amount` / `total_amount` (NUMERIC(10,2), NOT NULL)
  - `pdf_asset_url` (VARCHAR(500), NULLABLE), `created_at`
  - UNIQUE (`organization_id`, `invoice_number`)

- `trek_itinerary_days`
  - `id` (UUID, PK), `trek_id` (FK), `day_number` (INTEGER, NOT NULL), `title` (VARCHAR(150)), `description` (TEXT)
  - UNIQUE (`trek_id`, `day_number`)

- `itinerary_elements`
  - `id` (UUID, PK), `itinerary_day_id` (FK), `sequence` (INTEGER, NOT NULL), `element_type` (VARCHAR(20), CHECK `IN ('activity','meal','transport','accommodation','note')`), `title` (VARCHAR(150)), `description` (TEXT, NULLABLE), `time_of_day` (VARCHAR(20), NULLABLE)

- `trek_media`
  - `id` (UUID, PK), `trek_id` (FK), `asset_url` (VARCHAR(500), NOT NULL), `caption` (VARCHAR(255), NULLABLE), `sort_order` (INTEGER, NOT NULL, DEFAULT 0), `uploaded_by` (FK → users), `created_at`

- `trek_reviews`
  - `id` (UUID, PK), `trek_id` (FK), `booking_id` (FK, UNIQUE — one review per completed booking), `user_id` (FK), `rating` (INTEGER, CHECK `BETWEEN 1 AND 5`), `comment` (TEXT, NULLABLE), `created_at`

- `booking_participants`
  - `id` (UUID, PK), `booking_id` (FK), `name` (VARCHAR(150), NOT NULL), `age` (INTEGER, NULLABLE), `is_primary_contact` (BOOLEAN, NOT NULL, DEFAULT FALSE)

- `waitlist_entries`
  - `id` (UUID, PK), `trek_id` (FK), `user_id` (FK), `requested_slot_count` (INTEGER, NOT NULL, DEFAULT 1), `status` (VARCHAR(20), CHECK `IN ('waiting','offered','expired','converted')`), `offered_at` / `offer_expires_at` (TIMESTAMPTZ, NULLABLE), `created_at`

### 4.5 New — Communication

- `notification_consents`
  - `id` (UUID, PK), `user_id` (FK), `channel` (VARCHAR(10), CHECK `IN ('email','whatsapp','sms')`), `consent_type` (VARCHAR(15), CHECK `IN ('transactional','marketing')`), `granted_at` (TIMESTAMPTZ), `revoked_at` (TIMESTAMPTZ, NULLABLE)
  - UNIQUE (`user_id`, `channel`, `consent_type`)

- `notification_preferences`
  - `id` (UUID, PK), `user_id` (FK), `channel` (VARCHAR(10)), `is_enabled` (BOOLEAN, NOT NULL, DEFAULT TRUE)
  - UNIQUE (`user_id`, `channel`)

- `message_campaigns`
  - `id` (UUID, PK), `organization_id` (FK), `created_by` (FK → users), `channel` (VARCHAR(10)), `subject` (VARCHAR(200), NULLABLE — email only), `template_body` (TEXT, NOT NULL — contains merge-field placeholders), `audience_filter` (JSONB, NOT NULL — the segment-builder's serialized criteria), `status` (VARCHAR(10), CHECK `IN ('draft','tested','sent')`), `test_sent_to_email` / `test_sent_to_mobile` (VARCHAR, NULLABLE), `sent_at` (TIMESTAMPTZ, NULLABLE), `recipient_count` (INTEGER, NULLABLE)

- `message_campaign_recipients`
  - `id` (UUID, PK), `campaign_id` (FK), `user_id` (FK), `delivery_status` (VARCHAR(20)), `sent_at` (TIMESTAMPTZ, NULLABLE)

- `call_requests`
  - `id` (UUID, PK), `organization_id` (FK), `name` (VARCHAR(150), NOT NULL), `phone` (VARCHAR(20), NOT NULL), `preferred_time` (VARCHAR(100), NULLABLE), `message` (TEXT, NULLABLE), `status` (VARCHAR(10), CHECK `IN ('new','contacted','closed')`), `handled_by` (FK → users, NULLABLE), `created_at`

- `ticket_messages`
  - `id` (UUID, PK), `ticket_id` (FK), `sender_id` (FK → users), `message` (TEXT, NOT NULL), `created_at`

### 4.6 New — Operations

- `tenant_backups`
  - `id` (UUID, PK), `organization_id` (FK, NULLABLE — NULL means a full-platform backup), `triggered_by_user_id` (FK, NULLABLE), `triggered_by_platform_admin_id` (FK, NULLABLE), `storage_location` (VARCHAR(500), NOT NULL — encrypted, offsite), `status` (VARCHAR(10), CHECK `IN ('pending','completed','failed')`), `created_at`, `expires_at` (TIMESTAMPTZ, NOT NULL — enforces the retention window)

- `platform_access_logs` *(the meta-audit trail)*
  - `id` (UUID, PK), `platform_admin_id` (FK), `organization_id` (FK — the tenant whose data was accessed), `action` (VARCHAR(100), e.g. `'viewed_audit_log'`), `created_at`
  - *Separate from `audit_logs` deliberately — this table exists specifically to answer "who at the platform level looked at tenant X's data, and when," which is a narrower and more sensitive question than the tenant's own operational audit trail.*

### 4.7 Unchanged from TDR v1 (full definition there)
`user` core columns, `staff_profile` core columns, `trek` core columns, `payment` core columns beyond §4.3's additions, `password_reset_token`, and the existing `audit_logs` action-logging convention (now additionally scoped per-organization).

---

## 5. REST API Design

All endpoints listed in PDR v2 §14.1 follow the existing Flask-RESTful convention. A few deserve explicit request/response notes beyond the summary table:

- `POST /platform/organizations` — request: `{name, subdomain, tenant_admin_email, tenant_admin_name}`; response: `201 {organization_id, tenant_admin_temp_password}` (a default, forced-reset password, same pattern already used for staff onboarding in v1).
- `PUT /admin/roles/<id>` — request: `{name?, capability_keys: [...]}`; the route rejects the request with `422 {"error": {"code": "PROTECTED_CAPABILITY", ...}}` if `capability_keys` includes any capability flagged `is_protected` — this check is redundant with §6's enforcement but is kept here too as defense in depth at the API boundary.
- `POST /trekker/treks/<id>/book` *(extended)* — request now: `{participants: [{name, age?}, ...], coupon_code?}`; `slot_count` is derived server-side from `len(participants)`, never trusted from a client-supplied count field directly.
- `POST /admin/campaigns/<id>/test-send` — request: `{test_email, test_mobile}`; response renders the template against either a sample record or the composing user's own data, explicitly labeled as a preview, and never writes a `message_campaign_recipients` row (test sends are not real recipients).
- `POST /admin/campaigns/<id>/send` — only callable once `status='tested'`; server re-applies the consent filter (§6) at send time regardless of what the audience filter returned at draft time, since consent can change between drafting and sending.
- Every endpoint accepting a path-parameter ID accepts and returns the opaque encoded form (§6); the service layer decodes it to the internal integer/UUID immediately upon entry and never re-exposes the raw form.

---

## 6. Security Design

**Tenant isolation (structural, not conventional):** `TenantScopedMixin` is implemented as a SQLAlchemy mixin applying a global `with_loader_criteria` filter tied to `g.organization_id` at the session level, so a query against a tenant-scoped model is filtered automatically even if a developer forgets to add `.filter_by(organization_id=...)` by hand. This is the single most important security mechanism in this phase, given cross-tenant leakage is the platform's highest-rated risk (PDR v2 §12); the automated test suite includes a dedicated isolation-test class that attempts to read every tenant-scoped resource across an organization boundary and asserts it always fails.

**Protected capabilities (structural, not list-based):** a protected action is gated by a dedicated decorator, `@protected_capability_required`, which does **not** consult the `roles`/`role_capabilities` tables at all — it checks a direct ownership field instead: `current_user.id == g.organization.tenant_admin_user_id`. This guarantees no bug in the role-builder UI, no misconfigured `role_capabilities` row, and no future capability added to the menu by mistake can ever grant protected access, because the check structurally cannot read from the table a Tenant Admin can edit.

**Ordinary capability checks:** a `@requires_capability('key')` decorator loads the current staff user's `role_id` → joined `role_capabilities` → checks the required key is present. Denial is a `403`, matching every existing RBAC check's convention.

**Platform Admin authentication:** entirely separate session/login path from tenant users (§2); `@platform_admin_required` checks the `platform_admins` session exclusively and is never stacked with any tenant-aware decorator.

**Opaque identifiers:** new tables' UUID primary keys are exposed directly (UUIDs are already non-sequential and effectively non-enumerable). Existing v1 tables' integer primary keys are encoded at the HTTP boundary using a reversible Hashids-style scheme (a per-deployment salt, never the raw integer) — chosen over a full integer-to-UUID migration on live tables specifically to avoid a disruptive foreign-key rewrite across every already-populated v1 table. The internal integer remains the actual primary/foreign key throughout the codebase; only serialization at the route/template boundary changes.

**Consent enforcement at send time, not draft time:** `message_campaign` sends re-query `notification_consents` for the final recipient list at the moment of sending (§5), never relying on a snapshot taken when the audience was built, since consent state can change between drafting and sending a campaign.

**Carried forward unchanged from TDR v1:** CSRF via Flask-WTF, password hashing via Werkzeug's scrypt default, Flask-Login session management, Flask-Limiter on auth/reset endpoints (still single-instance-scoped per §15), SQL access exclusively through the ORM.

---

## 7. Frontend Technical Design

Unchanged in principle from TDR v1 for the reference implementation (Jinja2 + Bootstrap 5), but the operative architectural point for this phase is that **each tenant's actual production frontend is a separate deployment** consuming this backend's API and branding data (PDR v2 §5.2, §7) — the Jinja2 reference app continues to exist as the platform's own default/demo frontend and as the implementation target for this TDR's own templates (auth, admin tooling, Platform Admin console), but is not assumed to be what every rented tenant uses to present their public site.

---

## 8. Third-Party Integration Design

### 8.1 Email / SMS / WhatsApp (production-grade — supersedes TDR v1's MailHog-only design for this phase)
- **Email:** a dedicated transactional provider (e.g. SendGrid/Amazon SES) with proper SPF/DKIM/DMARC configuration, replacing raw SMTP for anything beyond local dev; MailHog remains the *local development and automated-test* transport only (TDR v1's offline-testing philosophy is preserved — production providers are mocked in the test suite exactly as Razorpay already is).
- **WhatsApp:** Meta's Cloud API or an approved Business Solution Provider, using pre-approved message templates for any business-initiated contact, per Meta's commerce policy. Every send checks `notification_consents` for `channel='whatsapp'` and the message's `consent_type` before dispatch.
- **SMS:** a standard SMS/OTP provider, used for mobile-number verification at registration and as a notification channel option.

### 8.2 Payment Gateway — Razorpay Route (supersedes TDR v1's single-account design)
- **Tenant onboarding:** each organization completes Razorpay's own linked-account KYC during tenant setup; `organizations.razorpay_route_account_id` is only populated once that completes, and manual/online booking for that tenant should not be enabled before it does.
- **Order creation:** unchanged mechanically from v1 (`razorpay.Order.create`), now additionally carrying transfer instructions for the tenant's linked account.
- **Settlement:** executed automatically by Razorpay at capture time per the transfer instructions; `payments.route_transfer_id` / `route_transfer_status` track the result for reconciliation.
- **Platform commission:** Route supports an automatic platform fee deducted at split time — **the percentage or flat amount is not yet decided** (flagged in §15) and is a blocking prerequisite before Route go-live, not a placeholder to default silently.
- **Compliance:** the Route pattern is adopted specifically because it keeps settlement from passing through a platform-held balance, which is the engineering mitigation for the Payment Aggregator exposure flagged in PDR v2 §12 — this TDR does not assert that this closes the regulatory question; that confirmation sits with the business's compliance advisor.

---

## 9. Infrastructure & DevOps

- **Database:** managed PostgreSQL (e.g. RDS/Cloud SQL), replacing the SQLite file. Migrations continue through Alembic/Flask-Migrate; the migration from SQLite requires a one-time data-export/import pass for any existing single-tenant data being onboarded as "tenant zero."
- **Multi-instance readiness:** the application itself may now run as more than one process/container (Postgres removes SQLite's single-writer constraint), **except** that rate limiting and scheduled-job execution (APScheduler, in-process) must stay pinned to exactly one instance until the deferred Redis migration (§15) lands — running multiple instances today would double-fire the stale-booking-expiry and ticket-purge jobs and desynchronize rate limits.
- **CI/CD:** the paused P6 CI workflow item is completed in this phase (P14), now additionally running the multi-tenant isolation test suite (§10) as a required gate, not an optional one.

---

## 10. Testing Strategy

- **Concurrency tests (re-derived):** the original `test_booking_race.py` is ported to run against Postgres with the new `FOR UPDATE` locking, and extended with a group-booking variant (§3.2) proving the generalized slot arithmetic still serializes correctly.
- **Tenant-isolation tests (new):** a dedicated test class that, for every tenant-scoped resource type, creates data under organization A and asserts every read/write attempt from an organization-B-scoped session fails — this is the test suite's single highest-priority addition given the risk profile of this phase.
- **Protected-capability tests (new):** asserts that no combination of role/capability assignment, however constructed through the role-builder API, ever grants access to a `@protected_capability_required` route for anyone other than the organization's `tenant_admin_user_id`.
- **Consent-filtering tests (new):** asserts a marketing-type campaign send never reaches a recipient whose only consent record is `transactional`, even when that recipient matches the campaign's audience filter.
- **Unit/integration/E2E layers:** unchanged in shape from TDR v1 (services-layer unit tests with mocks, Flask-test-client integration tests, Playwright E2E) — the paused E2E suite (P14) now additionally covers the public, non-login browsing flow and the Platform Admin tenant-onboarding flow as new first-class journeys.

---

## 11. Business Logic Algorithms

- **Refund calculation:** `refund_service.calculate_refund(booking, cancellation_reason)` — if `cancellation_reason in ('staff_force_cancel', 'identity_verification_failed')`, return 100% unconditionally, bypassing the tenant's `refund_policies` table entirely. If `'customer_requested'`, look up the organization's `refund_policies` rows, find the one whose day-range contains `days_until_trip`; if none matches, return 0% (the agreed default-to-zero-on-gap rule).
- **Waitlist promotion:** triggered from any slot-increment path (§3.5) — `waitlist_service.offer_next(trek)` finds the earliest `waiting` entries whose `requested_slot_count` fits the freed capacity, sets `status='offered'` with a fixed offer window (e.g. 2 hours), and schedules an expiry sweep (same APScheduler pattern as the existing stale-booking-expiry job) that reverts an unclaimed offer to `'expired'` and re-runs the promotion for the next entry in line.
- **GST invoice generation:** on `confirm_payment()`'s winner path (or a manual booking's sign-off), generate an `invoices` row with a per-organization sequential `invoice_number`, render the PDF server-side with the tenant's GST number and branding, and attach it to the booking-confirmation email.
- **Audience segment resolution:** `audience_service.resolve_segment(organization, filter_json)` translates the composer's stored `audience_filter` JSON into a scoped query (role, booking history, signup-date range, active status, etc.), always intersected with the consent table for the campaign's channel and type before being handed to the send step.

---

## 12. Error Handling & Logging

Unchanged in format and levels from TDR v1 (structured JSON logs, `request_id` threading, the standard error envelope). Two additions for this phase: every log line now also carries `organization_id` (or `null` for Platform Admin actions) so a single tenant's incident can be isolated in logs as easily as a single request already can; and `platform_access_logs` entries are written synchronously, in the same transaction as the cross-tenant read they're logging, so a logging failure can never silently allow an unlogged cross-tenant access.

---

## 13. Operational Runbook

```bash
# first-time platform setup (once, by the platform operator)
git clone <repo-url>
cd tma-platform
cp .env.example .env               # fill in Postgres, Razorpay Route, WhatsApp/email/SMS provider keys
docker compose build
docker compose run --rm web flask db upgrade        # Postgres migrations
docker compose run --rm web flask seed-platform-admin  # creates the first Platform Admin account
docker compose up web

# onboarding a new tenant (Platform Admin dashboard, or CLI equivalent for scripting)
docker compose run --rm web flask create-tenant \
  --name "Example Treks" --subdomain exampletreks \
  --admin-email admin@exampletreks.com

# running tests
docker compose run --rm test                         # full suite incl. tenant-isolation tests
docker compose run --rm test pytest -k concurrency -v   # race-condition suite (now Postgres-backed)
docker compose run --rm test pytest -k isolation -v     # cross-tenant isolation suite
```

---

## 14. Technical Dependencies & Licenses (additions over TDR v1)

| Package | Purpose | License |
|---|---|---|
| psycopg2 / psycopg | PostgreSQL driver | LGPL |
| razorpay (Route-enabled usage) | Split payments | MIT *(unchanged package, extended usage)* |
| weasyprint or reportlab | PDF generation (itineraries, invoices) | BSD / BSD |
| sendgrid / boto3 (SES) | Transactional email provider | MIT / Apache-2.0 |
| Meta WhatsApp Cloud API client (or BSP SDK) | WhatsApp messaging | Provider-specific ToS |
| An SMS provider SDK (e.g. Twilio) | OTP / SMS notifications | MIT *(client library; provider ToS applies to usage)* |
| hashids | Opaque ID encoding for legacy integer PKs | MIT |

---

## 15. Open Issues & Decisions Pending

- **Platform commission model.** Whether and how much of each transaction the platform retains via Route's fee-split has not been decided. This blocks §3.4/§8.2's implementation — it is a business decision, not an engineering gap.
- **Payment Aggregator compliance confirmation.** Route is the correct *engineering* pattern to minimize this exposure, but formal confirmation from the business's compliance advisor is a prerequisite for production go-live, not something this TDR can certify on its own.
- **ID/identity verification.** Fully scoped as a design fork in the Phase 7 decision log (capture format, gate-the-seat vs. gate-access-only, review workflow, auto-cancel deadline) — deliberately not built this phase. The `identity_verification_failed` cancellation-reason value is reserved in the schema (§4.3) specifically so this slots into the existing cancellation/refund mechanism without a further schema change when it's revisited.
- **Single-instance limitation on rate limiting and scheduled jobs.** Both currently assume exactly one running app instance; horizontal scaling is unsafe until a shared Redis-backed store replaces both, which is deliberately deferred (PDR v2 §3) rather than solved now.
- **Self-serve tenant onboarding.** Not designed this phase; `flask create-tenant` (§13) and the Platform Admin dashboard's manual registration flow are the only onboarding paths.
- **Generalized approval-chain / multi-sign-off system.** Only the hardcoded manual-booking sign-off exists; no configurable "requires a second approval" mechanism is built for other actions in this phase.
- **Platform/tenant pause capability.** Under business-owner reconsideration; no schema or route support exists for it in this design.
- **Expansion beyond India / non-Razorpay gateways.** Out of scope; the `payments.channel` enum and Route integration are both written assuming Razorpay as the only gateway.

---

## 16. Appendix — Environment Variable Reference (additions over TDR v1)

```bash
# Database (replaces SQLite config)
DATABASE_URL=postgresql://user:password@host:5432/tma_platform
TEST_DATABASE_URL=postgresql://user:password@host:5432/tma_platform_test

# Razorpay Route (extends the existing RAZORPAY_* block)
RAZORPAY_PLATFORM_ACCOUNT_ID=acc_xxxxxxxxxxxx
RAZORPAY_PLATFORM_COMMISSION_PERCENT=   # intentionally blank — see §15, not yet decided

# WhatsApp (Meta Cloud API or BSP)
WHATSAPP_PROVIDER=meta_cloud_api
WHATSAPP_ACCESS_TOKEN=xxxxxxxxxxxxxxxxxxxx
WHATSAPP_PHONE_NUMBER_ID=xxxxxxxxxxxxxxxxxxxx
WHATSAPP_BUSINESS_ACCOUNT_ID=xxxxxxxxxxxxxxxxxxxx

# Transactional email provider (replaces/extends MAIL_* for production)
EMAIL_PROVIDER=sendgrid
SENDGRID_API_KEY=xxxxxxxxxxxxxxxxxxxx

# SMS / OTP provider
SMS_PROVIDER=twilio
TWILIO_ACCOUNT_SID=xxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxx

# Opaque ID encoding
HASHIDS_SALT=change-me-per-deployment

# Reserved for a future phase — not yet wired (see §15)
REDIS_URL=
```

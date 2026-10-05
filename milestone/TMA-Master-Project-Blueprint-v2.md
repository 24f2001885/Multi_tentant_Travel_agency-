# MASTER PROJECT BLUEPRINT v2: TMA Platform — Multi-Tenant Trekking Management SaaS
### Docker + Git Milestone Workflow (P7–P14)

*Companion to the UI/UX Design Plan v2. Supersedes the original Master Project Blueprint for build-planning purposes from this point forward. The directory tree below is the end state of the application — it includes everything already delivered in P0–P6 (marked accordingly) plus everything this phase adds, so nothing built under P7–P14 should need to invent a new top-level folder, matching the same discipline the original Blueprint held itself to.*

---

## Source Reconciliation Note

This Blueprint is produced directly from Project Definition Report v2 and Technical Design Report v2, which are themselves the record of an extended decision-making process (the Phase 7 Decision Log) rather than a single upfront spec. Two points from that process are carried forward here as genuinely open rather than silently resolved, exactly as PDR v2 §3 and TDR v2 §15 already flag them:

1. **Platform commission model.** No milestone below can fully close out the Razorpay Route integration (P9) until the platform's commission percentage/flat-fee is decided — this is called out at that milestone rather than assumed.
2. **Payment Aggregator compliance confirmation.** P9's Route work proceeds on the assumption that the business's compliance advisor has confirmed this structure is appropriate; this Blueprint does not treat that confirmation as its own to grant.

Everything else below is treated as settled per PDR v2/TDR v2.

---

## 1. Unified Directory Structure

Final-state tree for the whole platform. Entries inherited unchanged from the P0–P6 build are marked `[P0-P6]`; everything else is new or modified in this phase and marked with its owning milestone.

```
tma-platform/
├── app/
│   ├── __init__.py                     # create_app() — P8/P9/P11/P12 each append tenant/RBAC/scheduler wiring
│   ├── extensions.py                   # [P0-P6] db, login_manager, mail, csrf, scheduler, limiter
│   │
│   ├── platform/                       # [P8] Platform Admin — structurally separate blueprint, own auth
│   │   ├── __init__.py
│   │   ├── routes.py                   # dashboard, organizations, onboarding, audit viewer, backups
│   │   ├── forms.py
│   │   └── auth.py                     # Platform Admin's own login path, separate from tenant auth entirely
│   │
│   ├── tenancy/                        # [P7] tenant resolution — not a blueprint, a core request-lifecycle module
│   │   ├── __init__.py
│   │   ├── resolver.py                 # before_request: host -> organization_id -> g.organization_id
│   │   └── scoping.py                  # TenantScopedMixin + the SQLAlchemy loader-criteria filter
│   │
│   ├── auth/                           # [P0-P6] + [P7] mobile/country-code + consent additions
│   │   ├── __init__.py
│   │   ├── decorators.py               # [P0-P6] role_required, staff_owns_trek, ticket_access_required
│   │   │                               # + [P8] requires_capability, protected_capability_required
│   │   ├── forms.py
│   │   └── routes.py
│   │
│   ├── admin/                          # Tenant Admin blueprint — [P0-P6] core + this phase's additions
│   │   ├── __init__.py
│   │   ├── forms.py
│   │   ├── routes.py                   # [P0-P6] trek/staff/user/bookings/payments/reports/audit/profile
│   │   ├── roles_routes.py             # [P8] roles & capabilities management
│   │   ├── refund_policy_routes.py     # [P9] per-tenant refund-tier configuration
│   │   ├── manual_booking_routes.py    # [P9] toggle + sign-off queue (both protected-capability gated)
│   │   ├── coupons_routes.py           # [P9]
│   │   ├── campaigns_routes.py         # [P11] composer, test-send, send
│   │   ├── call_requests_routes.py     # [P7]
│   │   └── backups_routes.py           # [P13] tenant self-serve backup
│   │
│   ├── staff/                          # [P0-P6] core + capability-gated additions
│   │   ├── __init__.py
│   │   ├── forms.py
│   │   └── routes.py                   # [P0-P6] dashboard/manage/participants/profile
│   │                                   # + [P10] create-trek, + [P9] manual-booking creation
│   │
│   ├── trekker/                        # [P0-P6] core + this phase's additions
│   │   ├── __init__.py
│   │   ├── forms.py
│   │   └── routes.py                   # [P0-P6] browse/detail/book/payment/bookings/profile
│   │                                   # + [P9] group participants, coupon apply, waitlist join
│   │                                   # + [P10] reviews, + [P11] notification preferences
│   │
│   ├── public/                         # [P7] anonymous-safe aliases of browse/detail — shares trekker/ templates
│   │   ├── __init__.py
│   │   └── routes.py                   # no @login_required; same service calls as trekker/routes.py's browse/detail
│   │
│   ├── tickets/                        # [P0-P6] shared blueprint + [P12] multi-turn rework
│   │   ├── __init__.py
│   │   ├── forms.py
│   │   └── routes.py                   # [P12] message-thread endpoints replace single-response flow
│   │
│   ├── api/                            # [P0-P6] webhook + full resource set
│   │   ├── __init__.py
│   │   ├── *_resource.py               # [P0-P6] unchanged
│   │   └── route_webhook_resource.py   # [P9] Razorpay Route transfer-status events
│   │
│   ├── services/                       # framework-agnostic business logic
│   │   ├── __init__.py
│   │   ├── auth_service.py             # [P0-P6] + [P7] mobile/country-code + consent-timestamp capture
│   │   ├── booking_service.py          # [P0-P6] + [P9] group-booking participant handling
│   │   ├── payment_service.py          # [P0-P6] + [P9] Route split settlement (SUPERSEDES locking strategy)
│   │   ├── ticket_service.py           # [P0-P6] + [P12] multi-turn message handling
│   │   ├── trek_service.py             # [P0-P6] + [P10] itinerary/media ownership rules
│   │   ├── notification_service.py     # [P0-P6] + [P11] production WhatsApp/email/SMS, consent checks
│   │   ├── history_service.py          # [P0-P6] unchanged
│   │   ├── tenancy_service.py          # [P8] organization/domain provisioning (used by platform/ routes)
│   │   ├── rbac_service.py             # [P8] role/capability assignment + protected-capability checks
│   │   ├── refund_service.py           # [P9] tiered calculation + force-cancel full-refund override
│   │   ├── waitlist_service.py         # [P9] offer/expiry/promotion logic
│   │   ├── coupon_service.py           # [P9]
│   │   ├── invoice_service.py          # [P9] GST invoice generation
│   │   ├── review_service.py           # [P10]
│   │   ├── itinerary_service.py        # [P10]
│   │   ├── media_service.py            # [P10]
│   │   ├── campaign_service.py         # [P11] merge-field rendering, test-send, consent-filtered send
│   │   ├── audience_service.py         # [P11] segment-filter resolution
│   │   ├── call_request_service.py     # [P7]
│   │   └── backup_service.py           # [P13]
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py                     # [P0-P6] + [P7] mobile fields, terms_accepted_at
│   │   ├── staff_profile.py            # [P0-P6] + [P8] role_id
│   │   ├── trek.py                     # [P0-P6] unchanged core
│   │   ├── booking.py                  # [P0-P6] + [P9] slot_count, channel, sign-off, cancellation fields
│   │   ├── booking_participant.py      # [P9]
│   │   ├── payment.py                  # [P0-P6] + [P9] channel, route_transfer fields
│   │   ├── ticket.py                   # [P0-P6] unchanged core (response fields kept for back-compat)
│   │   ├── ticket_message.py           # [P12]
│   │   ├── password_reset_token.py     # [P0-P6] unchanged
│   │   ├── audit_log.py                # [P0-P6] + [P7] organization_id scoping
│   │   ├── platform_admin.py           # [P8] — deliberately outside TenantScopedMixin
│   │   ├── platform_access_log.py      # [P8] the meta-audit trail
│   │   ├── organization.py             # [P7]
│   │   ├── domain.py                   # [P7]
│   │   ├── role.py                     # [P8]
│   │   ├── capability.py               # [P8]
│   │   ├── refund_policy.py            # [P9]
│   │   ├── coupon.py                   # [P9]
│   │   ├── coupon_redemption.py        # [P9]
│   │   ├── invoice.py                  # [P9]
│   │   ├── trek_itinerary_day.py       # [P10]
│   │   ├── itinerary_element.py        # [P10]
│   │   ├── trek_media.py               # [P10]
│   │   ├── trek_review.py              # [P10]
│   │   ├── waitlist_entry.py           # [P9]
│   │   ├── notification_consent.py     # [P11]
│   │   ├── notification_preference.py  # [P11]
│   │   ├── message_campaign.py         # [P11]
│   │   ├── message_campaign_recipient.py  # [P11]
│   │   ├── call_request.py             # [P7]
│   │   └── tenant_backup.py            # [P13]
│   │
│   ├── templates/
│   │   ├── shared/
│   │   │   ├── base.html               # [P0-P6] + [P7] cookie-consent banner include
│   │   │   ├── _footer.html            # [P7]
│   │   │   ├── _flash.html             # [P0-P6]
│   │   │   ├── _badge_macros.html      # [P0-P6] + this phase's new badge domains (§6 of UI/UX v2)
│   │   │   ├── sidebar_admin.html      # [P0-P6] + [P8] capability-driven nav rendering
│   │   │   ├── sidebar_staff.html      # [P0-P6] + [P8] capability-driven nav rendering
│   │   │   ├── sidebar_platform.html   # [P8] visually distinct Platform Admin shell
│   │   │   └── navbar_trekker.html     # [P0-P6] + [P7] shared with anonymous visitors
│   │   ├── auth/                       # [P0-P6] + [P7] mobile field, consent checkpoint
│   │   ├── platform/                   # [P8] dashboard, organizations, onboarding, audit, backups
│   │   ├── admin/                      # [P0-P6] core + [P8] roles, [P9] refund-policy/coupons/manual-booking,
│   │   │                               # [P11] campaigns, [P7] call-requests, [P13] backups
│   │   ├── staff/                      # [P0-P6] + [P10] create-trek, [P9] manual-booking form
│   │   ├── trekker/                    # [P0-P6] + [P9] group participants, [P10] reviews,
│   │   │                               # [P11] notification preferences, [P9] waitlist status
│   │   ├── tickets/                    # [P0-P6] + [P12] message-thread partial
│   │   ├── errors/                     # [P0-P6]
│   │   └── email/                      # [P0-P6] core + [P9] GST invoice, [P9] waitlist, [P9] force-cancel
│   │
│   └── static/                         # [P0-P6]; [P14] finally activates charts.js/form_validation.js/custom.css
│
├── migrations/                         # Alembic — [P7] re-baselined against Postgres
│   ├── env.py
│   └── versions/
│
├── tests/
│   ├── unit/                           # [P0-P6] + one new file per new service above
│   ├── integration/                    # [P0-P6] + new route-level tests per phase
│   ├── isolation/                      # [P8] NEW — dedicated cross-tenant data-isolation suite (TDR v2 §10)
│   │   └── test_tenant_scoping.py
│   ├── e2e/                            # [P14] Playwright suite resumed, extended with public-browsing
│   │                                   # and tenant-onboarding journeys
│   └── fixtures/                       # [P0-P6] + [P9] Route transfer fixtures, [P11] WhatsApp webhook fixtures
│
├── instance/                           # [P0-P6] — now unused once Postgres replaces the local SQLite file
│
├── .github/
│   └── workflows/
│       └── test.yml                    # [P14] resumed, now gating on tests/isolation/ as a required check
│
├── docker-compose.yml                  # [P7] postgres + web + mailhog(dev) + test services;
│                                       # a `redis` service is reserved/commented — not activated this phase
├── Dockerfile                          # [P0-P6] unchanged base image
├── requirements.txt                    # [P7] psycopg2, weasyprint/reportlab, provider SDKs, hashids
├── pyproject.toml / .flake8 / .pre-commit-config.yaml   # [P0-P6] unchanged
├── .env.example                        # [P7]/[P9]/[P11] extended — see TDR v2 §16
├── .gitignore
└── README.md
```

---

## 2. Git + Docker Workflow

**Branching (extends the P0–P6 convention):**
- `main` remains protected. Each phase gets its own branch, named for traceability back to this Blueprint: `phase/P7-platform-foundation`, `phase/P8-identity-access-governance`, `phase/P9-booking-money-engine`, `phase/P10-trek-content-discovery`, `phase/P11-communication-platform`, `phase/P12-support-v2`, `phase/P13-backup-monitoring-hardening`, `phase/P14-finish-p6-enhancements`.
- Merge to `main` via PR only after the phase's Docker test run passes — unchanged rule from P0–P6, now with one addition: **P8 onward, every phase's test run must also pass `tests/isolation/`**, not just its own new unit/integration tests, since any later phase could in principle reintroduce a cross-tenant leak through a new query.

**Commit convention:** unchanged — one meaningful commit per milestone item within a phase, e.g. `feat(tenancy): subdomain resolution and TenantScopedMixin`, `feat(rbac): dynamic role builder and protected-capability enforcement`.

**What "offline Docker testing" means now:** the same offline-first principle from TDR v1 §9–10 holds, extended to every new third-party integration this phase adds — Razorpay Route, WhatsApp, the production email provider, and SMS are all mocked/fixture-driven in the test suite exactly as Razorpay's original webhook and MailHog already were, so `docker compose run --rm test` still requires zero live network access beyond the initial `pip install`. The one infrastructure change: the `test` service now runs against a Postgres container, not a file-backed SQLite database — this is required, not optional, since the concurrency test suite (§10 of TDR v2) is specifically validating Postgres's `SELECT ... FOR UPDATE` locking, which has no SQLite equivalent to fall back to.

**Commit-vs-test-passing rule:** unchanged hard gate from P0–P6 — no commit is pushed to a phase branch until `docker compose run --rm test` passes locally for the code that commit adds, now including the isolation suite from P8 onward.

---

## 3. Milestone → Feature → Commit Map (P7–P14)

Ordered so each phase depends only on phases before it, exactly matching the dependency table in PDR v2 §10 — this ordering, not the order features happened to come up in discussion, is this document's main deliverable.

### **P7 — Platform Foundation**
*Branch: `phase/P7-platform-foundation`*
- Postgres migration (schema re-baselined via Alembic; one-time data migration path for any existing single-tenant data becoming "tenant zero").
- `organizations` and `domains` tables; `TenantScopedMixin` + the SQLAlchemy loader-criteria filter applied to every existing tenant-owned table; the `tenancy/` request-lifecycle resolver.
- Public, non-login browse/detail routes (`app/public/`), sharing `trekker/` templates.
- Opaque identifier scheme: UUID primary keys on every new table from this phase onward; Hashids encoding at the HTTP boundary for existing integer-keyed tables.
- Mandatory, country-code-validated mobile number on every account type; cookie-consent capture + T&C acceptance timestamp; sitewide footer + Request-a-Call form and its `call_requests` table.
- **Target:** the existing single-tenant application now runs correctly as "tenant zero" inside a genuinely multi-tenant-capable schema, with public browsing live and the legal/consent/footer baseline in place — nothing user-facing about bookings, payments, or roles has changed yet.
- **Git commit examples:** `feat(db): migrate schema to Postgres`, `feat(tenancy): organization/domain model and request-level resolution`, `feat(public): anonymous browse and trek detail routes`, `feat(security): opaque resource identifiers`, `feat(compliance): mandatory mobile number, cookie consent, footer`

### **P8 — Identity & Access Governance**
*Branch: `phase/P8-identity-access-governance`*
- `platform_admins` table and its wholly separate auth path; the `app/platform/` blueprint (dashboard, organization onboarding, cross-tenant audit viewer with its own `platform_access_logs` meta-audit).
- `roles`/`capabilities`/`role_capabilities` tables; the dynamic role-builder UI in `app/admin/roles_routes.py`; `requires_capability` and `protected_capability_required` decorators (the latter checking `organizations.tenant_admin_user_id` directly, never the role tables).
- The dedicated `tests/isolation/` suite, made a required CI gate from this phase forward.
- **Target:** a Platform Admin can onboard a new tenant end-to-end and never see that tenant's operational data except through the logged audit-viewer exception; a Tenant Admin can build and assign a fully custom role to a Staff member, and no combination of role-building can ever reach a protected capability.
- **Git commit examples:** `feat(platform): platform admin console and tenant onboarding`, `feat(rbac): dynamic role and capability model`, `feat(security): protected-capability enforcement`, `test(isolation): cross-tenant data access suite`

### **P9 — Booking & Money Engine v2**
*Branch: `phase/P9-booking-money-engine`*
- Re-derive the booking engine's locking strategy for Postgres (`SELECT ... FOR UPDATE` replacing `BEGIN IMMEDIATE`); port and extend `test_booking_race.py` with a group-booking variant.
- `booking_participants` + `slot_count` (group bookings); `waitlist_entries` + promotion/expiry jobs; slot-accounting correction wired to real cancel routes for both self-cancel (tiered refund) and force-cancel (apology notice, mandatory full refund via the shared `cancellation_reason` mechanism).
- `refund_policies` (per-tenant, seeded with the platform default, zero-on-gap behavior); `coupons`/`coupon_redemptions`; `invoices` (GST generation + email delivery).
- Manual/offline booking: the protected org-level toggle, Staff-side creation flow (with inline account creation for the trusted person), `payments.channel='offline'`, and the Tenant Admin sign-off queue.
- Razorpay Route integration — **blocked on a decided platform-commission model and confirmed PA-compliance sign-off before this milestone can close** (see Source Reconciliation Note above).
- **Target:** the flagship engine is race-safe on Postgres under group-booking load; every cancellation path corrects slot counts and applies the right refund rule; a tenant can configure their own refund policy and coupons; manual bookings work end-to-end short of real payment; Route settlement lands in each tenant's own account once the commission/compliance prerequisites are cleared.
- **Git commit examples:** `feat(booking): postgres row-locking and group-booking support`, `feat(booking): waitlist auto-promotion`, `feat(payments): per-tenant refund policy and coupon codes`, `feat(payments): manual/offline booking with tenant-admin sign-off`, `feat(payments): razorpay route split settlement`, `test(booking): postgres concurrency suite incl. group-booking race`

### **P10 — Trek Content & Discovery**
*Branch: `phase/P10-trek-content-discovery`*
- `trek_itinerary_days` + `itinerary_elements` (structured, day/element model) and PDF export; `trek_media` gallery; `trek_reviews` (gated to a completed booking, one per booking).
- Staff-created-trek flow: lands unassigned/`pending`, Tenant Admin assigns an owner and approves through the existing lifecycle gate.
- **Target:** every trek has a real, structured itinerary and a multi-image gallery visible on the public detail page; completed-trip Trekkers can leave a review; Staff can originate a new trek without Admin having to start it from scratch.
- **Git commit examples:** `feat(trek): structured itinerary with PDF export`, `feat(trek): media gallery`, `feat(trek): post-completion reviews`, `feat(staff): trek creation pending admin assignment`

### **P11 — Communication Platform**
*Branch: `phase/P11-communication-platform`*
- Production WhatsApp (Meta Cloud API/BSP), real email provider, and SMS integration, replacing MailHog for anything beyond local dev/test.
- `notification_consents` (transactional vs. marketing, per channel) and `notification_preferences`; consent enforcement moved into the send path itself, not left to campaign composition.
- `message_campaigns`/`message_campaign_recipients`: merge-field templating, the audience-segment builder (`audience_service`), mandatory test-send before any real send.
- Point-of-contact reveal-timing logic on trek communications.
- **Target:** a Trekker's notification preferences and consent are respected on every real send; Admin/Staff can compose, preview, and send a fully custom-segmented, consent-filtered campaign from their own dashboard.
- **Git commit examples:** `feat(comms): production whatsapp/email/sms integration`, `feat(comms): consent and notification-preference model`, `feat(comms): campaign composer with segment targeting and test-send`

### **P12 — Support v2**
*Branch: `phase/P12-support-v2`*
- `ticket_messages`; reworked ticket detail view as a running thread; the ticket stays open until the raising Trekker marks it resolved.
- **Target:** a ticket is a real back-and-forth conversation, not a single-shot response, while preserving every existing scoping rule (Staff never sees Query tickets, Admin sees everything, Trekker sees only their own).
- **Git commit examples:** `feat(tickets): multi-turn message threads`

### **P13 — Backup, Monitoring & Hardening**
*Branch: `phase/P13-backup-monitoring-hardening`*
- `tenant_backups`: Tenant Admin self-serve trigger/export; Platform Admin's own full-platform and per-tenant backup/restore, encrypted and offsite with a defined retention window.
- Platform Admin's business-load (bookings/users/revenue per tenant) and infrastructure-load (request volume, resource usage) monitoring views.
- Remaining security hardening carried over from the original plan's agreed-in-principle list (session management, indefinite audit-log retention with export tooling).
- **Target:** a tenant can recover their own data without Platform Admin involvement; Platform Admin has one dashboard answering both "is the business healthy" and "is the infrastructure healthy," per tenant and in aggregate.
- **Git commit examples:** `feat(backup): per-tenant self-serve and platform-level backup/restore`, `feat(platform): business and infrastructure load monitoring`, `feat(security): session hardening and audit-log retention tooling`

### **P14 — Finish Paused P6 Enhancements**
*Branch: `phase/P14-finish-p6-enhancements`*
- Chart.js dashboards actually rendered (the JSON endpoints already exist from the original P6); HTML5/JS client-side validation layer; the Bootstrap responsive pass across every screen built since P0, now including every new screen from P7–P13; the Playwright E2E suite resumed and extended with the public-browsing and tenant-onboarding journeys; the CI workflow finalized with the isolation suite as a required gate; final documentation/packaging.
- **Target:** every originally-scoped P6 enhancement is actually live, and the automated test net (unit, integration, isolation, concurrency, E2E) covers the platform end to end in CI, not just locally.
- **Git commit examples:** `feat(charts): chartjs dashboards per role`, `feat(validation): html5/js client-side validation`, `style(ui): bootstrap responsive pass`, `test(e2e): playwright suite incl. public browsing and tenant onboarding`, `ci: github actions workflow with isolation gate`

### Parked — not scheduled in any phase above
Identity/ID verification; a generalized multi-party approval-chain system beyond the one hardcoded manual-booking sign-off; a platform/tenant "pause" capability; self-serve tenant onboarding; a Redis-backed cache/rate-limiter/job-lock; expansion beyond India or beyond Razorpay. Each remains fully scoped in TDR v2 §15 for whenever it's picked back up, specifically so resuming it doesn't require re-deriving the design fork from scratch.

---

### Phase Summary Table

| Phase | Focus | Depends On | Primary Risk if Rushed |
| --- | --- | --- | --- |
| P7 | Platform Foundation | — | A missed `organization_id` filter anywhere here becomes a cross-tenant leak later |
| P8 | Identity & Access Governance | P7 | A protected capability accidentally made assignable is a privilege-escalation hole |
| P9 | Booking & Money Engine v2 | P7, P8 | A naive Postgres port of the locking strategy could silently reintroduce the double-booking race |
| P10 | Trek Content & Discovery | P7, partially P8 | Low risk — mostly additive content features |
| P11 | Communication Platform | P7, P8 | Sending marketing messages to transactional-only consenters risks WhatsApp policy enforcement |
| P12 | Support v2 | — (parallelizable) | Low risk — additive to an already-scoped flow |
| P13 | Backup, Monitoring & Hardening | P7, P8 | An untested restore path is a false sense of security |
| P14 | Finish Paused P6 Enhancements | — (interleave anywhere) | Low technical risk, but leaving it last means the automated safety net stays incomplete the longest |

P7 and P8 are the only true hard blockers for everything else — every other phase either depends on tenant-scoping existing (P7) or on the permission system existing (P8) before its own feature-specific work can be gated correctly. P10, P12, and P14 can run in parallel with P9/P11 once P7/P8 are merged, if more than one engineer is available.

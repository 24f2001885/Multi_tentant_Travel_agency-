# PROJECT DEFINITION REPORT v2: TMA Platform — Multi-Tenant Trekking Management SaaS

*Supersedes the original Project Definition Report for build-planning purposes from this point forward. v1 scoped a single-organization Trekking Management Application and was fully delivered (Milestones P0–P5 at 100% of the graded rubric; P6's two highest-priority enhancements — API Integration and Flask-Login/Security Hardening — also delivered; P6's remaining enhancements — Charts.js rendering, HTML5/JS validation, responsive UI pass, Playwright E2E suite, CI, and submission packaging — were explicitly paused, not abandoned, and are carried into this document's milestone map as P14.*

*This document does not re-litigate anything already built. It treats the delivered single-tenant application as the foundation one tenant now runs on, and scopes everything required to turn that foundation into a true multi-tenant, rentable SaaS platform serving many trekking companies from one shared backend.*

---

## 1. Executive Summary

TMA Platform is a multi-tenant Software-as-a-Service product that lets multiple independent trekking companies ("tenants") each run their own fully-branded trek-booking operation — public marketing site, booking engine, staff/role management, payments, support, and reporting — on one shared backend and one shared database, rented rather than individually built or hosted. Each tenant gets its own URL (subdomain or custom domain) and its own look and feel; underneath, every tenant's data, roles, payments, and policies are kept strictly isolated from every other tenant's, while the platform operator (the Platform Admin) retains a single pane of glass across all of them for provisioning, oversight, and financial reconciliation.

The core problem this solves is the same coordination problem the original TMA solved for one company — double bookings, stale slot counts, untracked staff assignments, no persistent trekking history — but now solved *once*, sold *many times*, with each tenant free to configure their own roles, refund policy, and branding without touching another tenant's instance or requiring a line of code to change per client.

---

## 2. Project Objectives

### Primary Objectives
- Convert the existing single-tenant application into a true multi-tenant platform: one shared backend and database, with every tenant's data isolated by organization, and each tenant's frontend served under its own subdomain or custom domain.
- Give every tenant's own administrator ("Tenant Admin") full, self-service control over their org's roles, staff permissions, refund policy, branding, and communications — with no engineering involvement required per client.
- Give the platform operator ("Platform Admin") a dedicated, tenant-isolated control plane: provisioning new tenants, cross-tenant financial visibility (via true split payments), business and infrastructure load monitoring, and platform-wide backup/audit oversight.
- Move payment collection to a true split-payment model so each tenant is paid directly into their own linked account, rather than the platform holding and redistributing tenant funds.
- Replace the single-tenant app's fixed role structure with a fully dynamic, tenant-defined permission system: Tenant Admin builds named roles from a capability menu and assigns them to staff, with a small, hardcoded set of protected capabilities that no role — however built — can ever grant.
- Open the platform to public, non-authenticated visitors: trek browsing, trek detail, media, and reviews must be visible without an account, per tenant.
- Deliver the full slate of business features confirmed for this build: group bookings, a waitlist, coupon codes, a per-tenant configurable refund policy, GST-compliant invoicing, structured/PDF-exportable itineraries, a media gallery per trek, trek reviews, production-grade WhatsApp/email/SMS communication with proper consent tracking, a dashboard-based campaign composer with full audience targeting, multi-turn ticketing, and staff-initiated manual/offline bookings.
- Raise the platform to company-grade security and compliance: opaque resource identifiers, hardened sessions, per-tenant self-service backup, and indefinite retention of sensitive audit events.

### Secondary Objectives (explicitly deferred, not dropped)
- Self-serve tenant onboarding (sign-up without Platform Admin involvement) — tenant onboarding remains manual for this phase.
- Identity/ID verification on bookings — fully scoped in an earlier design pass (see §15, Open Issues, in the companion Technical Design Report v2) but deliberately not built yet.
- A generalized "requires a second sign-off" approval-chain feature usable on any sensitive action — only the one hardcoded manual-booking sign-off exists in this phase.
- A Platform Admin "pause this tenant/platform" capability — under reconsideration by the business owner, no design or build work in this phase.
- Expansion beyond India / beyond Razorpay as a payment gateway.
- A shared caching layer (Redis-backed) for performance — explicitly held for a later phase, to be done together with the rate-limiter and background-job-locking fixes it shares a root cause with (see TDR v2 §15).

---

## 3. Scope of Work

### In-Scope
- **Multi-tenancy:** organization-scoped data model across the entire schema; subdomain + custom-domain tenant resolution; per-tenant branding (logo, colors, contact details, legal text) consumed by each tenant's own frontend.
- **Platform Admin control plane:** tenant registration (record + domain mapping + initial Tenant Admin account — not infrastructure provisioning), cross-tenant audit-log visibility with its own meta-audit trail, business-metric *and* infrastructure-load monitoring per tenant and in aggregate, platform-wide and per-tenant backup/restore, and visibility into net platform-wide and per-tenant payment flow through Razorpay Route.
- **Dynamic role & permission system:** Tenant Admin creates, edits, and deletes an unlimited number of named roles per org, built from a shared capability menu; staff permission changes take effect from the staff member's next login; every grant/revoke is audit-logged; a fixed, non-negotiable list of protected capabilities (tenant/organization settings, payment/payout configuration, manual-booking toggle and sign-off, role/capability management itself) can never be assigned to any role.
- **Public browsing:** anonymous landing, trek browse/search/filter, and trek detail pages per tenant, including media gallery and reviews.
- **Database migration:** SQLite → Postgres, with the booking engine's concurrency-safety re-derived for Postgres's locking model.
- **True split payments:** Razorpay Route, with each tenant linked to their own payout sub-account; the platform never holds tenant settlement funds.
- **Booking engine v2:** group bookings (one booking, multiple participants/slots), a waitlist with automatic notification on a freed slot, corrected slot accounting (increment on cancellation, decrement on confirmed payment) wired to real, callable routes, and two distinct cancellation paths — customer self-cancel (tiered refund) and Admin/Staff force-cancel (apology notice, mandatory full refund).
- **Manual/offline booking:** a per-tenant feature toggle (itself a protected capability), Staff-initiated with an account required for the booked person (created on the spot if needed), payment handled entirely outside the platform by the tenant's own team, every manual booking requiring Tenant Admin sign-off, and the booking's value still logged for the tenant's own finance and occupancy reporting.
- **Payments & pricing:** a fully tenant-configurable, tiered refund policy (day-ranges → refund percentage, pre-filled with a sensible default, any gap or unconfigured range resolving to 0% refund), coupon/discount codes, and GST-compliant invoices emailed alongside every booking-status update.
- **Trek content:** structured, day-and-element itinerary editable from the trek create/edit form (by Admin or the Staff member who created/is assigned to the trek), exportable as a PDF; a multi-image media gallery per trek; post-completion trek reviews.
- **Staff can create treks**, landing unassigned and in `pending` status until the Tenant Admin assigns a staff owner and approves it through the existing approval gate.
- **Communication, production-grade:** real WhatsApp (Meta Cloud API or an approved BSP) and email provider integration, with per-user, per-channel consent tracked separately for transactional and marketing messages; a per-user notification-preference center; an Admin/Staff dashboard composer supporting merge-field templating, full custom audience-segment targeting (not just "everyone on trek X"), a mandatory test-send to a chosen email/mobile before any real send, and a distinct marketing-send path that is filtered automatically to exclude anyone who hasn't consented to marketing contact; one named point-of-contact staff member shown per trek pre-departure, with the full on-ground team revealed only closer to the trek date.
- **Support v2:** tickets become multi-turn conversations that stay open, cycling replies between the raising Trekker and Staff/Admin, until the raising Trekker marks the ticket resolved.
- **Site-wide:** a footer (copyright, company details, a "request a call" form usable by both anonymous visitors and logged-in users) and cookie-consent/T&C capture at login and registration — content owned by the client's legal team, capture mechanism (timestamped consent record) owned by engineering.
- **Security hardening:** mandatory, country-code-validated mobile numbers for every role including Admin; opaque, non-enumerable identifiers in every user-facing URL and API response; and completing the remaining company-grade security items already agreed in principle (session hardening, rate limiting, audit-log integrity).
- **Backup:** self-serve, per-tenant backup/export triggerable by each Tenant Admin, in addition to Platform Admin's own full and per-tenant backup/restore capability.
- **Finishing the paused P6 enhancements:** Charts.js rendering, HTML5/JS client-side validation, the Bootstrap responsive pass, the Playwright E2E suite, the CI pipeline, and final documentation/packaging.

### Out-of-Scope (this phase)
- **Self-serve tenant signup.** Every tenant is onboarded manually by Platform Admin; a public "start your free trial" flow is not part of this build.
- **ID/identity verification on any booking**, manual or online. Fully scoped as a design fork, deliberately parked — see TDR v2 §15.
- **A general-purpose, configurable approval-chain system.** The only multi-party sign-off in this phase is the fixed, hardcoded Tenant Admin sign-off on manual bookings; no other action gets a configurable second-approval step in this phase.
- **Platform- or tenant-level "pause" capability.** Explicitly under owner reconsideration; no design or implementation this phase.
- **A Redis-backed cache, rate-limiter, or distributed job lock.** All three share the same single-instance limitation and are deliberately deferred together to a later phase rather than solved piecemeal now.
- **Non-Razorpay payment gateways, and operation outside India.** Split-payment work in this phase is scoped to Razorpay Route only.
- **Per-tenant backend template logic for ordinary pages.** Each tenant's frontend renders its own UI from branding *data* the backend supplies (name, logo, colors, contact info, legal text); the backend only owns templating for the artifacts that must be generated server-side regardless of frontend — transactional emails, GST invoices, and itinerary PDFs.
- **A platform commission/fee model.** Whether and how much of each transaction the platform retains via Razorpay Route's fee-split mechanism has not been decided — flagged for a business decision before Route is wired, not an engineering gap (see TDR v2 §15).

---

## 4. User Personas

- **Platform Admin (Owner).** Operates entirely outside any tenant's boundary — never a member of a tenant's role system, never visible to a tenant's own Admin hierarchy. Needs: onboard new tenants, see net platform-wide and per-tenant revenue through Razorpay Route, monitor both business health (bookings/users/revenue per tenant) and infrastructure load, pull full-platform or single-tenant backups, and — in narrowly scoped, self-auditing circumstances — read any tenant's internal audit log for support/compliance purposes. Success = the platform runs itself day-to-day; Platform Admin only intervenes for onboarding, oversight, and incident response.

- **Tenant Admin.** The single administrator of one trekking company's instance (the role the original single-tenant Admin now maps to). Needs: configure their org's branding, refund policy, and coupons; build and assign custom staff roles from the capability menu; approve/assign staff-created treks; sign off every manual booking; compose and send tenant-wide communications; trigger their own tenant's backups. Cannot create or promote other Admins, touch another tenant's data, or grant any staff member a protected capability. Success = can run their entire operation and shape their team's access without ever filing an engineering request.

- **Trek Staff.** A coordinator whose actual day-to-day capabilities are whatever their Tenant Admin has assigned them — anything from "handles finance/reporting only" to "handles ticket responses only" to a broad operational role, built freely from the capability menu rather than a fixed job title. May now also create a trek (pending Tenant Admin approval/assignment) and, if given the manual-booking capability, book a trusted person on their behalf with payment handled outside the platform. Success = has exactly the access their role needs, no more, no less, and it's easy for their Admin to adjust.

- **Trekker (end customer).** Browses and books without ever needing to register first; books for a group in one transaction; joins a waitlist when a trek is full and gets notified automatically if a slot frees up; leaves a review after a completed trek; controls their own notification preferences and what they've consented to receive; can self-cancel a booking under their tenant's refund policy, or — if a Staff/Admin force-cancels on their behalf — is guaranteed a full refund and an explanatory notice. Success = a frictionless, trustworthy booking experience indistinguishable in quality from a company that built this platform just for themselves.

- **Trusted/Manually-Booked Person.** Someone a trekking company wants added to a trek directly (a VIP, a walk-in, a phone booking) without going through online payment. Needs an account (created by Staff on the spot if they don't have one); everything about their experience — confirmations, point-of-contact messaging, trek updates — behaves exactly like a normal booking; only the online-payment step is absent, with the tenant's own team settling payment separately.

---

## 5. Functional Requirements

### 5.1 Platform Administration
Platform Admin operates from a dedicated control plane, structurally and technically separate from every tenant's own admin system (not a "super-tier" within it). Capabilities: register a new tenant (organization record, subdomain/custom-domain mapping, initial branding, first Tenant Admin account); view net money flow through the platform's Razorpay Route configuration, broken down per tenant; view business load (bookings, active users, revenue) and infrastructure load (request volume, resource usage) per tenant and in aggregate; trigger or review a full-platform or single-tenant backup/restore; and, in narrowly justified circumstances, read a tenant's internal audit log — an action which is itself logged in a separate meta-audit trail, since this is the one deliberate exception to total tenant data isolation.

### 5.2 Multi-Tenancy & Branding
Every tenant-scoped table carries an organization identifier; every query in the system must be scoped by it, enforced structurally (not left to per-route discipline) given this is the single highest-risk failure mode in the whole platform. Tenant resolution happens per-request from the incoming subdomain or a mapped custom domain. Each tenant supplies branding data (name, logo, color scheme, contact details, legal document text) which their own separately-deployed frontend renders however they choose; the one exception is system-generated documents (transactional emails, GST invoices, itinerary PDFs), which use a shared template structure with the tenant's branding data injected in, since these are generated server-side regardless of frontend.

### 5.3 Dynamic Role & Permission System
Tenant Admin builds an unlimited number of named roles per org from a shared menu of granular capabilities (e.g. `treks.edit`, `finance.view`, `tickets.respond`), and assigns one role to each staff member. A small, fixed set of protected capabilities — tenant settings, payment/payout configuration, the manual-booking toggle and sign-off, and role/capability management itself — can never be included in any role, by any Tenant Admin, under any circumstances; these are enforced through a separate mechanism entirely outside the flexible role system, checked directly against tenant ownership rather than against any assignable permission. A permission change takes effect the next time the affected staff member logs in, not mid-session. Every role created, edited, deleted, and every capability grant or revoke, is audit-logged.

### 5.4 Public Browsing & Discovery
Each tenant's landing page, trek browse/search/filter, and individual trek detail pages (including media gallery and reviews) are visible to anonymous visitors with no account required, scoped to that tenant's own treks only.

### 5.5 Authentication & Account Management
Mobile number is mandatory, with country-code-aware validation, for every account type — Trekker, Staff, and Tenant Admin alike. Cookie consent and Terms & Conditions acceptance are captured at login/registration as a timestamped record per user; the legal text itself is supplied and owned by each client's legal team. Every user-facing identifier (in URLs and API responses) is opaque rather than a guessable sequential number, closing the information-leak and enumeration risk that sequential IDs create even where ownership checks already block unauthorized access.

### 5.6 Trek Content Management
A trek's itinerary is modeled as a structured sequence of days, each with its own ordered elements (activity, meal, transport, accommodation, note) — not a single free-text field — editable from the same create/edit form as the rest of the trek's details, and exportable as a branded PDF. A trek carries a full image gallery rather than a single cover photo. Staff may create a new trek, which lands unassigned and in `pending` status; Tenant Admin assigns it to a staff owner and approves it through the trek lifecycle's existing approval gate before it can ever go live.

### 5.7 Booking Engine v2
A single booking may cover a group — one transaction, multiple participants, multiple slots consumed atomically. When a trek is fully booked, a Trekker may join a waitlist; if a slot later frees up (through cancellation or an abandoned-checkout expiry), the platform automatically notifies the earliest waitlisted entries with a time-limited window to claim it. Slot accounting is corrected and wired end-to-end: a confirmed booking decrements `available_slots` by its participant count; any cancellation — self-initiated or forced — increments it back by the same amount. Two distinct cancellation paths exist: a Trekker-initiated cancellation is subject to their tenant's configured refund-tier policy; an Admin/Staff-initiated force-cancellation always triggers an apology notice explaining the reason and always results in a full (100%) refund, regardless of the tenant's tiered policy — the same underlying mechanism intended to later cover a failed-ID-verification cancellation once that feature is built.

### 5.8 Manual/Offline Booking
Gated behind a per-tenant feature toggle that is itself a protected capability — only the Tenant Admin can turn it on or off for their org. When enabled, Staff may book a trusted person directly; that person must have an account, which Staff may create on their behalf if one doesn't exist. The platform does not process payment for a manual booking — that is handled entirely outside the system, by the tenant's own team — but the booking and its stated value are still recorded, feeding finance and trek-occupancy reporting (e.g. "N of M slots on Trek X came from manual bookings, worth ₹Y"). Every manual booking requires explicit Tenant Admin sign-off before it's considered confirmed. Every other part of the experience — confirmation messaging, point-of-contact communication, trek updates — behaves identically to an online booking; only the payment step and the added sign-off differ. Identity verification for manually-booked people is deliberately out of scope for now, same as for every other booking path.

### 5.9 Payments & Payouts
Payments are split at the transaction level via Razorpay Route: each tenant is linked to their own payout sub-account during onboarding, and funds settle to the tenant directly rather than passing through a platform-held balance. Each tenant configures their own tiered refund policy — a table of day-ranges-before-trip mapped to a refund percentage — pre-filled with a sensible default (14+ days: 100%; 7–13 days: 50%; 1–6 days: 25%; day of or after: 0%) and fully editable; any cancellation falling into a gap the tenant's table doesn't cover resolves to a 0% refund rather than erroring. Coupon/discount codes (percentage or flat, with optional expiry, usage caps, and minimum-booking-amount rules) provide pricing flexibility beyond a trek's flat price. A GST-compliant invoice is generated and emailed alongside every booking-status update.

### 5.10 Reviews
A Trekker may leave a rating and review on a trek only after completing a booking on it, surfaced publicly on that trek's detail page.

### 5.11 Communication & Marketing
WhatsApp (via Meta's Cloud API or an approved Business Solution Provider) and a real transactional email provider, plus SMS, are the platform's production communication channels — not demo stubs. Consent is tracked per user, per channel, and separately for transactional versus marketing contact, since WhatsApp's commerce policy prohibits unsolicited business-initiated marketing messages; a per-user notification-preference center lets Trekkers manage what they receive and how. Admin/Staff compose messages from their own dashboard using merge-field templating (e.g. a recipient's name or trek details substituted automatically, never by hand per recipient), target a fully custom audience segment built from criteria like role, booking history, or signup date — not limited to a single trek's participant list — send a one-off test copy to a chosen email and mobile number to review exactly what recipients will see, and only then commit to the real send; any send automatically excludes recipients who haven't consented to the message's category (marketing sends never reach transactional-only consenters, regardless of who built the segment). Each trek shows Trekkers exactly one named point-of-contact staff member before departure; the full on-ground team is revealed only as the trek date approaches.

### 5.12 Support / Ticketing v2
A ticket is a running conversation, not a single response: Staff/Admin and the raising Trekker exchange messages back and forth, with the ticket staying open and available for further replies until the raising Trekker — and only the raising Trekker — marks it resolved.

### 5.13 Site-Wide
Every page carries a footer with copyright and company details, plus a "request a call" form usable by anonymous visitors and logged-in users alike. A cookie-consent banner and T&C acceptance checkpoint appear at login/registration, with the legal text supplied by the client's own legal team and the acceptance itself recorded with a timestamp.

### 5.14 Backup & Disaster Recovery
Each Tenant Admin can trigger and download a backup/export of their own tenant's data, self-service, without Platform Admin involvement. Platform Admin separately holds full-platform and per-tenant backup/restore capability for disaster-recovery and offboarding purposes. Backups are stored encrypted and offsite, with a defined retention window.

---

## 6. Non-Functional Requirements

- **Security:** every tenant-scoped query enforced to filter by organization; a fixed, code-level (not database-only) protected-capabilities list that no dynamic role can ever include; opaque resource identifiers platform-wide; hardened session cookies (`HttpOnly`, `SameSite=Lax`, `Secure` outside local dev) with a bounded session lifetime; rate limiting on authentication and password-reset endpoints; passwords hashed via a modern, salted scheme; CSRF protection on every form and JSON-mutating endpoint; indefinite retention of audit-log entries covering financial actions, permission grants/revokes, and cross-tenant Platform Admin access.
- **Performance:** booking and dashboard routes target <300ms p95 response time under normal load; the slot-confirmation path must remain race-condition-safe under Postgres's locking model at the concurrency levels a multi-tenant platform implies (meaningfully higher than the original single-tenant demo scale).
- **Scalability:** the application must be safe to run as more than one instance; anything currently relying on in-process state (rate limiting, scheduled-job locking) is explicitly flagged as needing a shared external store before horizontal scaling is attempted, even though that store's introduction is deferred past this phase.
- **Compliance:** GST-compliant invoicing on every booking; WhatsApp messaging practice conforming to Meta's commerce and opt-in policy; the split-payment structure built to the pattern (Razorpay Route) intended to keep the platform outside Payment Aggregator territory, with that read confirmed by the business owner's own compliance advisor rather than assumed from engineering design alone.
- **Accessibility:** WCAG 2.1 AA, carried forward unchanged from v1, applied per-tenant since each tenant controls their own frontend implementation.

---

## 7. System Architecture (Overview)

The backend remains a single, shared Flask application — not a per-tenant deployment — serving data and business logic to an arbitrary number of independently branded, separately deployed tenant frontends, each reached through its own subdomain or mapped custom domain. A tenant-resolution step runs at the start of every request, establishing which organization the request belongs to before any data is read or written. The database moves from SQLite to Postgres, both to support real concurrent multi-tenant load and because Postgres's row-locking model (`SELECT ... FOR UPDATE` / advisory locks) replaces SQLite's single-writer `BEGIN IMMEDIATE` approach the original booking engine depended on. Payments route through Razorpay's Route product so that settlement happens directly to each tenant's own linked account rather than through a platform-held balance. A shared external store (Redis) for caching, rate limiting, and distributed job locking is architecturally anticipated but deliberately not introduced in this phase.

---

## 8. Database Schema (Summary)

Every tenant-scoped entity from the original single-tenant schema (`User`, `StaffProfile`, `Trek`, `Booking`, `Payment`, `Ticket`, `PasswordResetToken`, `AuditLog`) is extended with an organization reference and remains otherwise structurally the same unless noted below. New entities introduced for this phase: `Organization` (the tenant record, including branding and payout-linkage data) and `Domain` (subdomain/custom-domain → organization mapping); `Role` and `Capability` (with a join table, plus a `StaffProfile → Role` reference) implementing the dynamic permission system; `RefundPolicy` (per-organization tiered rules); `Coupon` and `CouponRedemption`; `Invoice` (GST records); `TrekItineraryDay` and `ItineraryElement`; `TrekMedia`; `TrekReview`; `BookingParticipant` (group-booking line items) and a `slot_count` on `Booking`; `WaitlistEntry`; `NotificationConsent` and `NotificationPreference`; `MessageCampaign` and `MessageCampaignRecipient` (the dashboard composer); `CallRequest`; `TenantBackup`; and a wholly separate `PlatformAdmin` table, deliberately outside the `User`/organization model entirely, since Platform Admin belongs to no tenant. Full column-level detail for every table is specified in the companion Technical Design Report v2.

---

## 9. Technology Stack

- **Backend:** Flask (Python 3.x), Flask-Login, Flask-RESTful — carried forward from v1.
- **Database:** PostgreSQL via SQLAlchemy ORM, replacing SQLite.
- **Payments:** Razorpay, using the Route product for split/multi-party settlement.
- **Communications:** Meta WhatsApp Cloud API (or an approved BSP) for WhatsApp; a dedicated transactional email provider (e.g. SendGrid/Amazon SES) replacing raw SMTP for production volume; an SMS provider for OTP and transactional SMS.
- **Documents:** a server-side PDF generation library (e.g. WeasyPrint) for itinerary exports and GST invoices.
- **Caching / rate limiting / job coordination:** anticipated to be Redis-backed in a future phase; not introduced in this phase.
- **Frontend:** Jinja2/Bootstrap continues to serve as the reference implementation; each tenant's actual production frontend is a separate deployment consuming the shared backend's API and branding data.
- **DevOps:** Docker/Docker Compose, Git-based per-milestone branching — carried forward from v1's workflow.

---

## 10. Development Phases & Timeline

This picks up immediately after the delivered P0–P5 milestones (100% of the original graded scope), and folds in the remaining paused P6 enhancements alongside the new platform scope, ordered by genuine dependency rather than business priority alone.

| Phase | Focus | Depends on |
|---|---|---|
| **P7 — Platform Foundation** | Postgres migration + re-derived concurrency-safe locking; organization-scoped schema retrofit across every existing table; subdomain/custom-domain tenant resolution; public non-login browsing routes; opaque/non-enumerable resource identifiers; mandatory, country-code-validated mobile numbers for all roles; cookie-consent + T&C capture; site-wide footer and call-request form. | — |
| **P8 — Identity & Access Governance** | Platform Admin control plane (tenant registration, cross-tenant audit visibility + its own meta-audit trail); the dynamic role & capability system (role builder, protected-capabilities enforcement, live-at-next-login permission changes, full grant/revoke audit logging). | P7 |
| **P9 — Booking & Money Engine v2** | Slot-accounting fix wired to real cancel routes (self-cancel and force-cancel, both increment/decrement correctly); unified cancellation-reason model (customer-tiered vs. company-initiated-full-refund); group bookings; waitlist with auto-notify; manual/offline booking (protected-capability toggle, Tenant Admin sign-off, finance/occupancy logging only); per-tenant configurable refund-policy table with seeded default; coupon codes; Razorpay Route split-payment integration (contingent on compliance sign-off, including a decided platform commission model); GST invoice generation and delivery. | P7, P8 |
| **P10 — Trek Content & Discovery** | Structured day/element itinerary with PDF export; multi-image media gallery; trek reviews; staff-created-trek flow landing unassigned pending Admin assignment and approval. | P7, partially P8 (itinerary-edit permission scoping) |
| **P11 — Communication Platform** | Production WhatsApp/email/SMS integration; transactional-vs-marketing consent model and per-user notification-preference center; dashboard composer with merge-field templating, full audience-segment targeting, mandatory test-send, and consent-filtered real sends; point-of-contact reveal-timing logic. | P7 (mobile + consent), P8 (permission-gated composer access) |
| **P12 — Support v2** | Multi-turn ticketing (message-thread model, reworked ticket detail UI), open until the raising Trekker resolves it. | Can run in parallel with P9–P11 |
| **P13 — Backup, Monitoring & Hardening** | Per-tenant self-serve backup/export plus Platform Admin's full/per-tenant backup and restore; encrypted offsite storage with a defined retention window; business *and* infrastructure load monitoring for Platform Admin; remaining security hardening (session management, audit-log retention/export tooling). | P7, P8 |
| **P14 — Finish Paused P6 Enhancements** | Charts.js rendering, HTML5/JS client-side validation, the responsive Bootstrap pass, the Playwright E2E suite, the CI pipeline, and final documentation/packaging. | Can interleave anywhere; carries no new dependency |
| **Parked — revisit later, not scheduled** | ID/identity verification; a generalized approval-chain feature; Platform/tenant "pause" capability; self-serve tenant onboarding; Redis-backed caching/rate-limiting/job-locking; non-India / non-Razorpay expansion. | — |

---

## 11. Admin & Permission Model

Permissions are organized in two structurally separate tiers:

1. **Platform Admin** — a single, hardcoded tier, entirely outside every tenant's own role system. Not represented as a role any tenant can see, grant, or approximate. Has platform-wide provisioning, financial-visibility, monitoring, and backup authority, and a narrow, self-logging exception to read a tenant's own audit trail.
2. **Tenant-scoped roles** — per organization, defined entirely by that tenant's own Tenant Admin via the dynamic role/capability system (§5.3). There is no fixed "Operations Admin" or "Finance Admin" tier baked into the platform; those are, at most, optional starter-role templates a Tenant Admin may use, rename, or fully customize. The one fixed exception within a tenant is the set of **protected capabilities**, which can never be included in any role a Tenant Admin builds, by design:

| Protected Capability | Rationale |
|---|---|
| Toggle the manual-booking feature for the org | Controls whether payment can ever be bypassed for a booking |
| Sign off an individual manual booking | The one deliberate, non-delegable human checkpoint on an unpaid booking |
| Configure payment/payout settings (Route linkage, bank details) | Directly controls where the tenant's money goes |
| Create, edit, or delete roles/capabilities | Prevents a staff member from ever granting themselves or others new power |
| Edit organization-level settings, branding, legal text, or domain mapping | Controls the tenant's identity and legal posture |
| Delete or deactivate the organization itself | An irreversible, business-level action |
| Any action that would reach across organizations | Structurally enforced regardless of role — never a grantable permission at all |

---

## 12. Risk Analysis & Mitigation

- **Cross-tenant data leakage.** The single biggest risk in this platform: a query missing an organization filter could leak one tenant's data into another's view. *Mitigation:* organization-scoping enforced structurally at the data-access layer (not left to per-route discipline), with a mandatory code-review checklist item for any new tenant-scoped model, and dedicated isolation tests in the automated suite.
- **Payment-aggregator regulatory exposure.** Collecting and redistributing money on behalf of multiple independent businesses can trigger Payment Aggregator obligations. *Mitigation:* true split payment via Razorpay Route (money settles directly to each tenant, minimizing platform fund-holding), confirmed against actual regulatory requirements by the business's own compliance advisor before launch — an engineering pattern alone does not close this risk.
- **Dynamic-permission privilege escalation.** A flexible role-builder could, if built carelessly, let a staff member be granted (or grant themselves) platform-breaking power. *Mitigation:* the protected-capabilities list (§11) is enforced entirely outside the assignable-role mechanism, checked directly against tenant ownership rather than against any grantable permission.
- **WhatsApp policy violation.** Sending a marketing message to someone who only consented to transactional contact risks Meta/BSP enforcement action against the account. *Mitigation:* consent type is checked and filtered automatically at send time, not left to whoever composes a campaign to remember.
- **Single-instance fragility.** Rate limiting, scheduled-job execution, and (eventually) caching all currently assume one running app instance; horizontal scaling before this is fixed would cause silent double-execution or inconsistent limits. *Mitigation:* explicitly flagged as a prerequisite before scaling past one instance, with the fix (a shared Redis store) scoped but deliberately deferred.
- **Postgres concurrency re-derivation risk.** The original booking engine's race-condition safety was built specifically around SQLite's locking semantics; a naive port to Postgres could silently reintroduce the double-booking race it was designed to prevent. *Mitigation:* the locking strategy is explicitly re-derived (not assumed equivalent) and re-validated against the same concurrency test suite that proved the original design.

---

## 13. Success Metrics

### Technical KPIs
- Zero cross-tenant data leakage incidents.
- Booking/payment concurrency test suite passes deterministically on Postgres, matching the reliability bar already proven on SQLite.
- <300ms p95 response time on booking and dashboard routes under realistic multi-tenant load.

### Business KPIs
- Time to onboard a new tenant from signed agreement to live, bookable storefront.
- Per-tenant payment-split accuracy (settled amounts reconcile exactly against bookings, net of any agreed platform fee).
- Tenant self-service rate: proportion of role, refund-policy, and branding changes made without engineering involvement.

### User Adoption KPIs
- WhatsApp/email opt-in compliance rate (zero marketing sends to transactional-only consenters).
- Waitlist-to-booking conversion rate once a slot frees up.
- Review submission rate on completed treks.

---

## 14. Appendices

### 14.1 API Endpoint Summary (additions over v1)

| Method | Path | Purpose |
|---|---|---|
| POST | `/platform/organizations` | Platform Admin creates a new tenant |
| GET | `/platform/organizations/<id>/audit-logs` | Platform Admin reads a tenant's audit trail (self-logging) |
| GET | `/platform/stats` | Business + infrastructure load, per tenant and aggregate |
| POST | `/platform/organizations/<id>/backups` | Platform Admin triggers a tenant backup |
| GET / POST / PUT / DELETE | `/admin/roles` | Tenant Admin manages dynamic roles |
| PUT | `/admin/staff/<id>/role` | Assign a role to a staff member |
| GET / PUT | `/admin/settings/refund-policy` | View/edit the tenant's refund-tier table |
| PUT | `/admin/settings/manual-booking` | Protected: toggle manual booking for the org |
| POST | `/staff/bookings/manual` | Staff creates a manual/offline booking |
| POST | `/admin/bookings/<id>/manual-sign-off` | Protected: Tenant Admin signs off a manual booking |
| POST | `/trekker/treks/<id>/book` *(extended)* | Now accepts a participant count/list for group booking |
| POST / GET | `/trekker/treks/<id>/waitlist` | Join / view waitlist status |
| POST / GET | `/admin/coupons` | Create / list coupon codes |
| POST | `/trekker/bookings/<id>/review` | Submit a post-completion review |
| GET | `/treks/<id>/reviews` | Public review listing |
| POST | `/admin/treks/<id>/itinerary` | Create/update structured itinerary |
| GET | `/treks/<id>/itinerary/pdf` | Download itinerary as PDF |
| POST / DELETE | `/admin/treks/<id>/media` | Manage a trek's media gallery |
| POST | `/admin/campaigns` | Draft a composed message/campaign |
| POST | `/admin/campaigns/<id>/test-send` | Send a one-off preview copy |
| POST | `/admin/campaigns/<id>/send` | Commit the real, consent-filtered send |
| GET / PUT | `/trekker/notification-preferences` | View/edit channel preferences |
| GET / POST | `/tickets/<id>/messages` | Multi-turn ticket conversation |
| GET / POST | `/admin/backups` | Tenant Admin's self-serve backup |
| POST | `/public/call-requests` | Anonymous "request a call" submission |

### 14.2 Notification Templates (additions over v1)
- **"Waitlist Slot Available"** — triggered when a freed slot is offered to a waitlisted Trekker, with a claim deadline.
- **"Booking Cancelled by [Company]"** — the apology/explanation notice accompanying every Staff/Admin force-cancellation, always paired with a full-refund confirmation.
- **"GST Invoice"** — sent alongside each booking-status update.
- **"We'd love your review"** — sent after a trek is marked completed.
- **Test-send preview** — not a real recipient-facing template, but the exact rendered output of any composed campaign, shown to the composing Staff/Admin before the real send.
- **"Thanks — we'll call you back"** — acknowledgment for a submitted call-request.

### 14.3 Glossary (additions over v1)
- **Tenant / Organization:** one independent trekking company operating on the shared platform.
- **Platform Admin:** the platform operator's own administrative role, structurally outside every tenant.
- **Tenant Admin:** the single administrator of one tenant's instance; the role the original single-tenant "Admin" now maps to.
- **Capability:** one granular, assignable permission (e.g. `tickets.respond`).
- **Protected Capability:** a capability that can never be included in any tenant-built role, enforced outside the role system entirely.
- **Split Payment (Razorpay Route):** a payment architecture where a transaction settles directly to a tenant's own linked account rather than through a platform-held balance.
- **Point-of-Contact:** the one staff member named to Trekkers on a given trek before the full on-ground team is revealed.
- **Manual/Offline Booking:** a Staff-created booking for a trusted person, with payment handled entirely outside the platform.
- **Consent (Transactional vs. Marketing):** two separately tracked permissions a user grants per communication channel; marketing sends must never reach a transactional-only consenter.
- **Audience Segment:** a custom-filtered recipient set built for a dashboard-composed message, not limited to a single trek's participants.

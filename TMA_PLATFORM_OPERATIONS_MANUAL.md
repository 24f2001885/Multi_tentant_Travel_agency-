# TMA Multi-Tenant Platform — Complete Operations & Implementation Manual (Milestones P7 to P14)

**Platform:** TMA (Trek Management Application) Multi-Tenant Enterprise Engine  
**Covered Milestones:**  
- **P7:** Tenancy Core, Subdomain Routing & Auth Hardening  
- **P8:** Identity, Granular RBAC & Access Governance  
- **P9:** Booking & Money Engine v2 (Coupons, Invoices, Waitlist, Razorpay Route)  
- **P10:** Trek Content, Discovery, Structured Itineraries & Reviews  
- **P11:** Omnichannel Communication Platform (Email, WhatsApp, SMS, Meta Webhooks)  
- **P12:** Support v2 (Multi-turn Ticketing & Running Conversation Threads)  
- **P13:** Backup, Monitoring & Hardening (Self-Serve Tenant Backups, Platform DR, Load Dashboards)  
- **P14:** Paused P6 Enhancements (HTML5 Validation, Dynamic Chart.js, Responsive UI, CI & E2E Journeys)  

---

## 1. What Has Been Built & Completed (P7 $\to$ P14)

### Milestone P7: Tenancy Core & Security Hardening
- **Subdomain Routing & Host Resolution:** Dynamically matches subdomains (`<subdomain>.localhost:5000`) and custom domains to `Organization` records. Dedicated Platform Admin control plane hosted at `platform.localhost:5000`.
- **Tenant Isolation Engine:** Universal `TenantScopedMixin` automatically binds `organization_id` (UUID) to database models. Contextual execution via `tenant_context(org_id)` and `tenant_bypass()`.
- **Cross-Host Barrier:** Tenant routes return HTTP 404 when requested from the platform host; platform control-plane routes return HTTP 404 from tenant hosts.
- **Session Security:** Strict host-only session cookies (no wildcards) prevent cross-tenant credential leakage.
- **Auth Hardening:** E.164 phone normalization via Google's `phonenumbers` library; mandatory Terms of Service acceptance gate (`terms_accepted_at`).

### Milestone P8: Identity & Access Governance (RBAC)
- **Granular Capability Catalog:** Centralized capability definition covering treks, bookings, money, staff, and settings.
- **Custom Role Builder:** Tenant owners can define custom roles with selective capability sets (`Role`, `role_capabilities`).
- **Starter Roles Seeding:** Idempotently provisions standard starter roles (*Lead Guide*, *Trek Coordinator*, *Support Specialist*, *Finance Manager*, *Content Editor*).
- **Privilege Escalation Protection:** High-security administrative capabilities (`roles.manage`, `staff.manage`, `org.settings.manage`) are strictly reserved for the Organization Owner / Tenant Admin. Non-owner staff attempts are rejected with HTTP 403 / 422.
- **Session Performance Snapshots:** Fast role capability snapshots stored in the session for zero-database overhead checks (`@role_required` and `@has_capability`).

### Milestone P9: Booking Engine v2 & Money Platform
- **Multi-Participant Bookings:** `BookingParticipant` model captures primary trekker + companion details, medical conditions, and emergency contacts.
- **Promotional Coupons Engine:** Flat and percentage discounts (`Coupon`, `CouponRedemption`) with concurrency validation, max usage limits, minimum order amounts, and tenant scoping.
- **Tiered Refund Engine:** Automatic cancellation refund calculations based on time brackets (`RefundPolicy`).
- **Automated Invoicing & WeasyPrint PDF:** Sequential tax-compliant invoices (`Invoice`) with GST calculations and downloadable branded PDFs.
- **Automated Waitlist FIFO Engine:** `WaitlistEntry` queue with automatic sweeps upon booking cancellations and expiring offer windows (`WAITLIST_OFFER_WINDOW_MINUTES`).
- **Manual / Offline Booking Sign-Off:** Dual-phase capture for cash/NEFT bookings requiring staff sign-off and audit timestamps.
- **Razorpay Route Integration:** Split payment transfers with platform commission deductions and webhook settlement (`RouteWebhookResource`).

### Milestone P10: Trek Content, Discovery & Itinerary Engine
- **Structured Multi-Day Itineraries:** Day-by-day itineraries (`TrekItineraryDay`) with typed elements (`ItineraryElement`: activity, meal, transport, accommodation, note).
- **Vector PDF Itinerary Export:** Printable, branded trek brochures with complete day breakdowns.
- **Media Uploads & EXIF Sanitization:** Image uploads (`TrekMedia`) with Pillow-based automatic EXIF/GPS metadata stripping and UUID storage.
- **Staff Trek Creation Workflow:** Staff trek submissions enter `pending` status without staff assignment; only tenant admins can approve and publish inventory.
- **Verified Trekker Reviews:** Post-trek review submissions (`TrekReview`) restricted strictly to verified completed bookings, with privacy name masking (e.g., "Rahul K.").

### Milestone P11: Communication Platform & Messaging Compliance
- **Omnichannel Dispatch Gateway:** Single entry-point `dispatch()` in `app/services/notification_service.py` supporting Email, SMS, and WhatsApp Cloud API.
- **Critical Notification Carve-Out:** High-priority transactional alerts (booking confirmation, cancellation, waitlist offer, password reset OTP) bypass marketing preference opt-outs.
- **Consent & Preference Management:** Explicit consent ledger (`NotificationConsent`), channel switches (`NotificationPreference`), and RFC 8058 one-click unsubscribe links and headers.
- **Marketing Message Campaigns:** Campaign builder (`MessageCampaign`) with audience targeting (`audience_service.py`), mandatory safety test-send gate, and per-recipient tracking (`MessageCampaignRecipient`).
- **Meta WhatsApp Cloud API Webhook:** Cryptographic signature verification (`X-Hub-Signature-256`), hub challenge verification, and monotonic delivery receipt tracking (`sent` $\to$ `delivered` $\to$ `read`).

### Milestone P12: Support v2 (Multi-turn Ticketing)
- **Multi-turn Thread Model:** Database table `ticket_messages` captures individual replies within an inquiry thread.
- **Running Conversation Lifecycle:** A ticket stays open and available for back-and-forth messages until the **raising Trekker, and only the raising Trekker**, marks it resolved.
- **Preserved Staff Scoping:** Staff users can only access problem tickets for treks assigned to them; query tickets remain Admin-only.
- **Thread UI:** Threaded conversation view in `detail.html` displaying participant roles and reply form.

### Milestone P13: Backup, Monitoring & Hardening
- **Self-Serve Tenant Backups:** Tenant Admin can trigger and download an encrypted/compressed `.json.gz` archive of all tenant data (`/admin/backups`).
- **Cross-Tenant Backup Isolation:** Strict scoping prevents Tenant A from downloading Tenant B's backups (HTTP 404).
- **Platform Disaster Recovery:** Platform Admin can generate full platform snapshots or targeted tenant exports (`/platform/backups`).
- **Business & Infrastructure Load Monitoring:** Real-time dashboards (`/platform/monitoring`) tracking GMV volume, commission take-rates, disk usage, scheduler jobs, and table row counts.
- **Immutable Meta-Audit Trail:** `PlatformAccessLog` tracks all superadministrator cross-tenant accesses.

### Milestone P14: Paused P6 Enhancements, Validation & CI
- **Client-Side Form Validation:** `app/static/js/form_validation.js` provides instant feedback, E.164 phone checks, and date range sanity.
- **Dynamic Chart.js Rendering:** `app/static/js/charts.js` unifies line, bar, and doughnut charts across dashboards and reports.
- **Bootstrap Responsive Pass:** `app/static/css/custom.css` optimizes table scrolling, card grids, and mobile layouts.
- **Automated CI Workflow:** `.github/workflows/test.yml` enforces the cross-tenant isolation suite (`pytest tests/isolation/`) as a required blocking gate on every PR.
- **E2E User Journeys:** `tests/e2e/test_journeys.py` validates public browsing, registration, and tenant onboarding.

---

## 2. Local Machine Setup & Runbook

### Step 1: Configure Windows Hosts File
Add these lines to `C:\Windows\System32\drivers\etc\hosts`:
```text
127.0.0.1 platform.localhost
127.0.0.1 tenantzero.localhost
127.0.0.1 a.localhost
127.0.0.1 b.localhost
```

### Step 2: Start Docker Containers
```powershell
docker compose build
docker compose up -d
```

### Step 3: Run Database Migrations & Seeds
```powershell
# Apply all database migrations (Baseline up through P13)
docker compose exec web flask db upgrade

# Seed Platform Superadmin, Tenant Zero, Admin, and RBAC
docker compose exec web flask seed-platform-admin
docker compose exec web flask seed-tenant-zero
docker compose exec web flask seed-admin
docker compose exec web flask seed-rbac
docker compose exec web flask backfill-consents
```

### Step 4: Run Test Suite
```powershell
docker compose run --rm test
```

---

## 3. Web Portals & URLs

| Portal | URL | Credentials |
| :--- | :--- | :--- |
| **Platform Superadmin** | `http://platform.localhost:5000` | `owner@platform.local` / `PlatformOwnerPass123!` |
| **Platform Monitoring** | `http://platform.localhost:5000/monitoring` | (Superadmin only) |
| **Platform Backups** | `http://platform.localhost:5000/backups` | (Superadmin only) |
| **Tenant Zero Portal** | `http://tenantzero.localhost:5000` | `admin@tma.local` / `AdminPass123!` |
| **Tenant Backups** | `http://tenantzero.localhost:5000/admin/backups` | (Tenant Admin only) |
| **MailHog Local Inbox** | `http://localhost:8025` | Zero-configuration local mailbox |

# TMA Multi-Tenant Platform — Milestones P7 to P11 Operations & Setup Manual

**Platform:** TMA (Trek Management Application) Multi-Tenant Enterprise Engine  
**Covered Milestones:**  
- **P7:** Tenancy Core, Subdomain Routing & Auth Hardening  
- **P8:** Identity, Granular RBAC & Access Governance  
- **P9:** Booking & Money Engine v2 (Coupons, Invoices, Waitlist, Razorpay Route)  
- **P10:** Trek Content, Discovery, Structured Itineraries & Reviews  
- **P11:** Omnichannel Communication Platform (Email, WhatsApp, SMS, Meta Webhooks)  

---

## 1. What Has Been Built & Completed (P7 $\to$ P11)

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

---

## 2. Local Machine Setup & Runbook

### Step 1: Configure Windows Hosts File
Because multi-tenancy relies on subdomains, your local machine must know how to route `*.localhost`:
1. Open PowerShell or Notepad as **Administrator**.
2. Open file: `C:\Windows\System32\drivers\etc\hosts`
3. Add the following lines at the bottom:
   ```text
   127.0.0.1 platform.localhost
   127.0.0.1 tenantzero.localhost
   127.0.0.1 a.localhost
   127.0.0.1 b.localhost
   ```
4. Save the file.

### Step 2: Initialize `.env`
Ensure your `.env` file exists in the project root with the following local defaults:
```env
# Flask
FLASK_APP=app:create_app
FLASK_ENV=development
SECRET_KEY=dev-secret-key-tma-multi-tenant
HASHIDS_SALT=dev-salt-hashids-tma

# Database
DATABASE_URL=postgresql://tma:tma_dev_password@db:5432/tma_platform
TEST_DATABASE_URL=postgresql://tma:tma_dev_password@db_test:5432/tma_platform_test

# Tenancy & Hosts
BASE_DOMAIN=localhost
PLATFORM_ADMIN_HOST=platform.localhost
TENANT_ZERO_NAME=Summit Expeditions
TENANT_ZERO_SUBDOMAIN=tenantzero

# Superadmin & Tenant Admin Seeds
PLATFORM_ADMIN_NAME=Platform Owner
PLATFORM_ADMIN_EMAIL=owner@platform.local
PLATFORM_ADMIN_PASSWORD=PlatformOwnerPass123!
ADMIN_EMAIL=admin@tma.local
ADMIN_PASSWORD=AdminPass123!

# Local Communications (MailHog capture)
EMAIL_PROVIDER=smtp
MAIL_SERVER=mailhog
MAIL_PORT=1025
MAIL_DEFAULT_SENDER=no-reply@tma.local
SMS_PROVIDER=twilio
WHATSAPP_PROVIDER=meta_cloud_api
```

### Step 3: Start Docker Desktop & Launch Containers
```powershell
# In PowerShell:
docker compose build
docker compose up -d
```

### Step 4: Run Migrations & Seeds
Run these commands in PowerShell to prepare your database:
```powershell
# 1. Apply all database migrations (P7 baseline up through P11)
docker compose exec web flask db upgrade

# 2. Seed Platform Superadmin
docker compose exec web flask seed-platform-admin

# 3. Seed Tenant Zero (default organization & primary subdomain)
docker compose exec web flask seed-tenant-zero

# 4. Seed the initial Tenant Administrator
docker compose exec web flask seed-admin

# 5. Seed the RBAC Capability Catalog and starter roles
docker compose exec web flask seed-rbac

# 6. Backfill communication consents for legacy accounts
docker compose exec web flask backfill-consents
```

### Step 5: Test URLs in Your Browser
- **Platform Superadmin Portal:** `http://platform.localhost:5000` (Log in with `owner@platform.local` / `PlatformOwnerPass123!`)
- **Tenant Zero Portal:** `http://tenantzero.localhost:5000` (Log in with `admin@tma.local` / `AdminPass123!`)
- **MailHog Local Inbox:** `http://localhost:8025` (View all outgoing verification and notification emails instantly)

---

## 3. External Third-Party APIs & Portals Guide

To switch from the local offline development environment to a live production deployment, you need credentials from the following third-party developer consoles:

| Provider | Purpose | Where to Register | Environment Variables Needed |
| :--- | :--- | :--- | :--- |
| **Razorpay** | Online booking payments & vendor splits | [Razorpay Dashboard](https://dashboard.razorpay.com/) | `RAZORPAY_KEY_ID`<br>`RAZORPAY_KEY_SECRET`<br>`RAZORPAY_PLATFORM_ACCOUNT_ID`<br>`RAZORPAY_ROUTE_WEBHOOK_SECRET` |
| **Meta (WhatsApp)** | WhatsApp booking alerts & campaign broadcasts | [Meta for Developers](https://developers.facebook.com/) | `WHATSAPP_ACCESS_TOKEN`<br>`WHATSAPP_PHONE_NUMBER_ID`<br>`WHATSAPP_BUSINESS_ACCOUNT_ID`<br>`WHATSAPP_WEBHOOK_VERIFY_TOKEN`<br>`WHATSAPP_APP_SECRET` |
| **Twilio** | SMS notifications | [Twilio Console](https://console.twilio.com/) | `TWILIO_ACCOUNT_SID`<br>`TWILIO_AUTH_TOKEN`<br>`TWILIO_FROM_NUMBER` |
| **SendGrid / SES** | Production Email delivery | [SendGrid](https://app.sendgrid.com/) or [AWS SES](https://aws.amazon.com/ses/) | `EMAIL_PROVIDER=sendgrid`<br>`SENDGRID_API_KEY`<br>*(or AWS SES SMTP credentials)* |

---

## 4. Development / Testing Zero-Cost Fallback Mode

You do **not** need to spend money or wait for Meta/Twilio business approvals while developing locally:
1. **Payments:** The application runs out of the box using Razorpay test mode (`rzp_test_...`). Dummy cards allow simulated transactions without real money.
2. **Emails:** Outgoing emails are intercepted by the local `mailhog` container. View captured emails at `http://localhost:8025`.
3. **SMS & WhatsApp:** If keys are omitted in development, the notification dispatcher logs outbound payloads to container logs rather than failing, enabling full end-to-end user flows without third-party dependencies.

#!/usr/bin/env bash
###############################################################################
# P9.sh — v2 MILESTONE 9: Booking & Money Engine v2
# TMA Platform — Multi-Tenant Trekking Management SaaS
#
# Covers (Master Project Blueprint v2 §3, phase P9):
#   - Group bookings (slot_count + booking_participants), generalized locking
#   - Waitlist with automatic offers
#   - Slot-accounting fix wired to REAL cancel routes (self-cancel tiered
#     refund / force-cancel full refund)
#   - Per-tenant refund policy, coupons, GST invoices
#   - Manual/offline booking (protected toggle + Tenant Admin sign-off)
#   - Razorpay Route split settlement (BLOCKED items below)
#
# Depends on: P7 (tenancy), P8 (RBAC/protected capabilities). Never writes real
# application logic — only git/dir/config setup + PROMPT seeds.
#
# BLOCKED / UNDECIDED (Blueprint v2 Source Reconciliation Note):
#   * Platform commission model — RAZORPAY_PLATFORM_COMMISSION_PERCENT is left
#     BLANK on purpose. Route settlement is seeded behind a guard: it only runs
#     when that value is set AND the org has razorpay_route_account_id.
#     Otherwise the booking still confirms, a WARNING is logged, and money stays
#     in the platform's single Razorpay account. DO NOT go live like that — it is
#     exactly the Payment-Aggregator exposure Route exists to avoid.
#   * PA compliance sign-off — not this script's (or engineering's) to grant.
#
# FLAGGED DECISIONS:
#  1. Columns NOT in TDR v2 but required: organizations.manual_booking_enabled
#     (BOOLEAN default FALSE — where the protected toggle lives),
#     organizations.last_invoice_number (INTEGER default 0 — per-org invoice
#     sequence under row lock). New child tables also carry organization_id via
#     TenantScopedMixin (TDR §1 hard gate) although §4.4 omits it.
#  2. WAITLIST TRIGGER: TDR §3.5 names the confirmation-loser and stale-expiry
#     paths as slot-freeing, but under "no soft hold" (§3.4) they never took a
#     slot. Real freeing events = cancel of a 'booked' booking (self/force) and
#     Staff/Admin RAISING available_slots. Hooks are seeded there only.
#  3. WAITLIST OFFERS DON'T RESERVE (no soft hold). Seeded interpretation: while
#     an offer is live, initiate_booking() by anyone else sees availability
#     reduced by the live offered slots. Confirm, or offers become best-effort.
#  4. COUPONS: redeemed at confirmation (coupon_redemptions has no status
#     column), so used_count may overshoot max_uses by in-flight checkouts.
#     Discount is capped so payable never drops below ₹1.00 (Razorpay minimum).
#  5. REFUNDS: TDR v2 never says whether TMA calls Razorpay's refund API or only
#     records status (v1 PDR: status only). Seeded as status-recording + a
#     clearly-named initiate_refund() hook. A 0% refund leaves payment_status
#     'paid'. Confirm before real money moves.
#  6. GST: rate (GST_RATE_PERCENT, blank => invoice skipped with WARNING) and
#     inclusive-vs-exclusive pricing are undecided — seeded as price-inclusive.
#     Cancellation credit notes are not in the TDR. Invoice PDFs are written to
#     instance/invoices/ (local disk; multi-instance caveat) and served only via
#     authenticated routes. Needs org.gst_number, else skipped.
#  7. MANUAL BOOKING: slots decrement at sign-off (no hold); Staff may book any
#     OPEN trek in their org (capability-scoped by Tenant Admin); stale-booking
#     expiry MUST skip manual bookings; staff-created accounts get NULL
#     terms_accepted_at (completion gate fires at first login); a "Reject"
#     action on the sign-off queue is a small addition for completeness.
#  8. days_until_trip uses a fixed Asia/Kolkata calendar day (India-only scope).
#  9. New env vars beyond TDR v2 §16: RAZORPAY_ROUTE_WEBHOOK_SECRET,
#     REQUIRE_ROUTE_LINKAGE (default false; gates ONLINE booking until Route is
#     verified), GST_RATE_PERCENT, WAITLIST_OFFER_WINDOW_MINUTES.
# 10. Extra file beyond the Blueprint tree: email/gst_invoice_pdf.html (the PDF
#     document template, distinct from the email body).
# 11. Dockerfile is rewritten (backup kept) to add WeasyPrint's system libs.
#
# USAGE:
#   cd ~/Desktop/t
#   bash ./docs/P9.sh
###############################################################################

set -euo pipefail

BRANCH_NAME="phase/P9-booking-money-engine"
LOG_FILE="./docs/p9_v2_milestone.log"

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
log "===== P9.sh (v2) started — Booking & Money Engine v2 ====="

# -----------------------------------------------------------------------------
# STEP 0: Pre-flight
# -----------------------------------------------------------------------------
echo "STEP 0: Pre-flight checks..."
[ -d ".git" ] || { echo "ERROR: no .git found."; exit 1; }
for f in app/tenancy/scoping.py app/services/rbac_service.py app/services/tenancy_service.py \
         app/services/payment_service.py app/services/booking_service.py \
         app/services/notification_service.py app/platform/routes.py; do
  [ -f "$f" ] || { echo "ERROR: $f missing — run P7.sh/P8.sh first."; exit 1; }
done
log "STEP 0 complete: pre-flight checks passed"

# -----------------------------------------------------------------------------
# STEP 1: Branch
# -----------------------------------------------------------------------------
echo "STEP 1: Checking out milestone branch..."
checkout_phase_branch "phase/P8-identity-access-governance" "$BRANCH_NAME"
log "STEP 1 complete: on $BRANCH_NAME (base: phase/P8-identity-access-governance)"

# -----------------------------------------------------------------------------
# STEP 2: Directories
# -----------------------------------------------------------------------------
echo "STEP 2: Creating directories..."
mkdir -p tests/fixtures tests/unit tests/integration instance/invoices
log "STEP 2 complete: tests/fixtures, instance/invoices created"

# -----------------------------------------------------------------------------
# STEP 3: Infra/config (final content — config, not application logic)
# -----------------------------------------------------------------------------
echo "STEP 3: Writing infra/config..."
[ -f Dockerfile ] && cp Dockerfile Dockerfile.p8.bak

cat << 'SEEDEOF' > Dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["flask", "run", "--host=0.0.0.0"]
SEEDEOF
seed_note "Dockerfile"

ensure_req "weasyprint>=60,<70"
seed_note "requirements.txt (P9: weasyprint)"

if ! grep -q "TMA Platform v2 (P9)" .env.example; then
cat << 'SEEDEOF' >> .env.example

# --- TMA Platform v2 (P9) ---
RAZORPAY_PLATFORM_ACCOUNT_ID=acc_xxxxxxxxxxxx
RAZORPAY_PLATFORM_COMMISSION_PERCENT=   # intentionally blank — NOT yet decided (see P9.sh header)
RAZORPAY_ROUTE_WEBHOOK_SECRET=
REQUIRE_ROUTE_LINKAGE=false
GST_RATE_PERCENT=                       # blank => GST invoices skipped with a WARNING
WAITLIST_OFFER_WINDOW_MINUTES=120
SEEDEOF
fi
seed_note ".env.example"
echo "  !! Copy the new keys from .env.example into your real .env."
log "STEP 3 complete: Dockerfile, requirements.txt, .env.example"

# -----------------------------------------------------------------------------
# STEP 4: Models
# -----------------------------------------------------------------------------
echo "STEP 4: Seeding models..."

cat << 'SEEDEOF' > app/models/booking_participant.py
"""
FILE PURPOSE: The `booking_participants` table — one row per person on a
(group) booking (TDR v2 §4.4).

PROMPT FOR LLM IMPLEMENTATION:
1. Inherit TenantScopedMixin (see header FLAG 1). Columns: id (UUID PK),
   booking_id (Integer FK bookings.id NOT NULL, ON DELETE CASCADE), name
   (String(150) NOT NULL), age (Integer null), is_primary_contact (Boolean
   NOT NULL default False).
2. Partial unique index on (booking_id) WHERE is_primary_contact — at most
   one primary contact per booking (postgresql_where).
3. Relationship back to Booking (Booking.participants, ordered, cascade
   delete-orphan).

DEBUGGING:
# print(f"[DEBUG] participant booking={self.booking_id} primary={self.is_primary_contact}")

OFFLINE DOCKER TEST CASES:
- Two primary-contact participants on one booking raise IntegrityError.
- Deleting a booking removes its participants.
"""
SEEDEOF
seed_note "app/models/booking_participant.py"

cat << 'SEEDEOF' > app/models/refund_policy.py
"""
FILE PURPOSE: The `refund_policies` table — a tenant's tiered refund rules
(TDR v2 §4.4; PDR v2 §5.9).

PROMPT FOR LLM IMPLEMENTATION:
1. Inherit TenantScopedMixin. Columns: id (UUID PK), min_days_before_trip
   (Integer NOT NULL, CHECK >= 0), max_days_before_trip (Integer null — NULL
   means "and above"; CHECK >= min when not null), refund_percentage
   (Numeric(5,2) NOT NULL, CHECK BETWEEN 0 AND 100).
2. Index (organization_id, min_days_before_trip). Gaps/overlaps are NOT
   DB-enforced — refund_service validates edits; an uncovered day-count
   resolves to 0% at calculation time.

DEBUGGING:
# print(f"[DEBUG] refund_policy {self.min_days_before_trip}-{self.max_days_before_trip} => {self.refund_percentage}%")

OFFLINE DOCKER TEST CASES:
- refund_percentage=101 raises IntegrityError; max < min raises IntegrityError.
"""
SEEDEOF
seed_note "app/models/refund_policy.py"

cat << 'SEEDEOF' > app/models/coupon.py
"""
FILE PURPOSE: The `coupons` table — tenant discount codes (TDR v2 §4.4).

PROMPT FOR LLM IMPLEMENTATION:
1. Inherit TenantScopedMixin. Columns: id (UUID PK), code (String(30) NOT
   NULL, upper-cased via @validates), discount_type (String(10) NOT NULL,
   CHECK IN ('percentage','flat')), discount_value (Numeric(10,2) NOT NULL,
   CHECK > 0; percentage additionally <= 100), valid_from/valid_until (Date
   null), max_uses (Integer null), used_count (Integer NOT NULL default 0),
   min_booking_amount (Numeric(10,2) null), is_active (Boolean NOT NULL
   default True). UniqueConstraint(organization_id, code).

DEBUGGING:
# print(f"[DEBUG] coupon {self.code} type={self.discount_type} used={self.used_count}/{self.max_uses}")

OFFLINE DOCKER TEST CASES:
- Same code in two orgs is allowed; twice in one org raises IntegrityError.
- Lower-case input is stored upper-case.
"""
SEEDEOF
seed_note "app/models/coupon.py"

cat << 'SEEDEOF' > app/models/coupon_redemption.py
"""
FILE PURPOSE: The `coupon_redemptions` table — one row per redeemed coupon
(TDR v2 §4.4).

PROMPT FOR LLM IMPLEMENTATION:
1. Inherit TenantScopedMixin. Columns: id (UUID PK), coupon_id (UUID FK),
   booking_id (Integer FK bookings.id, UNIQUE — one coupon per booking),
   user_id (Integer FK users.id), discount_applied (Numeric(10,2) NOT NULL),
   created_at (DateTime(timezone=True) default now).

DEBUGGING:
# print(f"[DEBUG] redemption coupon={self.coupon_id} booking={self.booking_id} discount={self.discount_applied}")

OFFLINE DOCKER TEST CASES:
- A second redemption for the same booking_id raises IntegrityError.
"""
SEEDEOF
seed_note "app/models/coupon_redemption.py"

cat << 'SEEDEOF' > app/models/invoice.py
"""
FILE PURPOSE: The `invoices` table — GST invoice records (TDR v2 §4.4).

PROMPT FOR LLM IMPLEMENTATION:
1. Inherit TenantScopedMixin. Columns: id (UUID PK), booking_id (Integer FK
   bookings.id, UNIQUE), invoice_number (String(50) NOT NULL — sequential per
   organization), taxable_amount / gst_amount / total_amount (Numeric(10,2)
   NOT NULL), pdf_asset_url (String(500) null — holds an INTERNAL storage
   path under instance/invoices/, never a public URL), created_at.
   UniqueConstraint(organization_id, invoice_number).

DEBUGGING:
# print(f"[DEBUG] invoice {self.invoice_number} total={self.total_amount}")

OFFLINE DOCKER TEST CASES:
- Same invoice_number in two orgs allowed; twice in one org raises
  IntegrityError. A second invoice for one booking raises IntegrityError.
"""
SEEDEOF
seed_note "app/models/invoice.py"

cat << 'SEEDEOF' > app/models/waitlist_entry.py
"""
FILE PURPOSE: The `waitlist_entries` table (TDR v2 §4.4).

PROMPT FOR LLM IMPLEMENTATION:
1. Inherit TenantScopedMixin. Columns: id (UUID PK), trek_id (Integer FK
   treks.id), user_id (Integer FK users.id), requested_slot_count (Integer
   NOT NULL default 1, CHECK >= 1), status (String(20) NOT NULL default
   'waiting', CHECK IN ('waiting','offered','expired','converted')),
   offered_at / offer_expires_at (DateTime(timezone=True) null), created_at.
2. Partial unique index (user_id, trek_id) WHERE status IN
   ('waiting','offered') — a user holds at most one live entry per trek.
3. Index (trek_id, status, created_at) for FIFO promotion.

DEBUGGING:
# print(f"[DEBUG] waitlist user={self.user_id} trek={self.trek_id} status={self.status}")

OFFLINE DOCKER TEST CASES:
- A second 'waiting' entry for the same user+trek raises IntegrityError; an
  'expired' one does not block a new entry.
"""
SEEDEOF
seed_note "app/models/waitlist_entry.py"

cat << 'SEEDEOF' >> app/models/booking.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (modify this model, TDR v2 §4.3):
1. Add slot_count (Integer NOT NULL default 1, CHECK > 0), booking_channel
   (String(10) NOT NULL default 'online', CHECK IN ('online','manual')),
   manual_sign_off_by (Integer FK users.id null), manual_sign_off_at
   (DateTime(timezone=True) null), cancellation_reason (String(30) null,
   CHECK IN ('customer_requested','staff_force_cancel','payment_failed',
   'identity_verification_failed') — last value reserved, unused),
   cancelled_by (Integer FK users.id null), refund_percentage_applied
   (Numeric(5,2) null), coupon_id (UUID FK coupons.id null).
2. Relationship `participants` (see booking_participant.py).
3. The uq_active_booking partial index is unchanged.

DEBUGGING:
# print(f"[DEBUG] booking {self.id} slots={self.slot_count} channel={self.booking_channel} reason={self.cancellation_reason}")

OFFLINE DOCKER TEST CASES:
- slot_count=0 raises IntegrityError; booking_channel='phone' raises.
- Existing rows default to slot_count=1, booking_channel='online'.
"""
SEEDEOF
seed_note "app/models/booking.py (P9 addendum)"

cat << 'SEEDEOF' >> app/models/payment.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (modify this model, TDR v2 §4.3):
1. Add channel (String(10) NOT NULL default 'razorpay', CHECK IN
   ('razorpay','offline')), route_transfer_id (String(100) null),
   route_transfer_status (String(20) null, CHECK IN
   ('pending','processed','failed')).
2. gateway_order_id becomes NULLABLE, with CHECK (channel <> 'razorpay' OR
   gateway_order_id IS NOT NULL).
3. Offline payments: amount = the stated value, status 'initiated' until the
   Tenant Admin sign-off flips it to 'success'.

DEBUGGING:
# print(f"[DEBUG] payment {self.id} channel={self.channel} route={self.route_transfer_status}")

OFFLINE DOCKER TEST CASES:
- channel='razorpay' with gateway_order_id None raises IntegrityError;
  channel='offline' with None is accepted.
"""
SEEDEOF
seed_note "app/models/payment.py (P9 addendum)"

cat << 'SEEDEOF' >> app/models/organization.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (modify this model; FLAG 1):
1. Add manual_booking_enabled (Boolean NOT NULL default False) and
   last_invoice_number (Integer NOT NULL default 0).
2. Property `is_route_verified`: True when razorpay_route_account_id is set
   (the "Route linkage: Verified" badge source).

DEBUGGING:
# print(f"[DEBUG] org {self.id} manual={self.manual_booking_enabled} route_ok={self.is_route_verified}")

OFFLINE DOCKER TEST CASES:
- New orgs default manual_booking_enabled=False, last_invoice_number=0.
"""
SEEDEOF
seed_note "app/models/organization.py (P9 addendum)"

cat << 'SEEDEOF' >> app/models/__init__.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Import and add to __all__: BookingParticipant, RefundPolicy, Coupon,
   CouponRedemption, Invoice, WaitlistEntry.

DEBUGGING:
# print(f"[DEBUG] models package exports {len(__all__)}")

OFFLINE DOCKER TEST CASES:
- db.metadata.tables now also contains booking_participants, refund_policies,
  coupons, coupon_redemptions, invoices, waitlist_entries.
"""
SEEDEOF
seed_note "app/models/__init__.py (P9 addendum)"
log "STEP 4 complete: models seeded"

# -----------------------------------------------------------------------------
# STEP 5: Services
# -----------------------------------------------------------------------------
echo "STEP 5: Seeding services..."

cat << 'SEEDEOF' > app/services/refund_service.py
"""
FILE PURPOSE: Tiered refund calculation + policy management (TDR v2 §11;
PDR v2 §5.7, §5.9). Framework-agnostic.

PROMPT FOR LLM IMPLEMENTATION:
1. `calculate_refund(booking, reason, today=None) -> Decimal`: reason in
   ('staff_force_cancel','identity_verification_failed') -> 100 unconditionally
   (bypasses the policy table). 'customer_requested' -> find the org's
   refund_policies row whose [min,max] day-range contains days_until_trip
   (trek.start_date minus today, Asia/Kolkata calendar day); no row -> 0
   (default-to-zero-on-gap, never an error).
2. `preview_refund(booking) -> (percentage, amount)` for the cancel confirm UI
   (amount = payment.amount * pct / 100, rounded to 2dp).
3. `seed_default_policy(org)`: idempotent; rows 14+ days:100, 7–13:50,
   1–6:25, 0–0:0 (PDR v2 §5.9).
4. `validate_policy_rows(rows)`: min<=max, 0<=pct<=100, no OVERLAPPING ranges;
   gaps allowed (they mean 0%). Raise PolicyValidationError with row numbers.
5. `replace_policy(org, rows, actor)`: validate then atomically replace the
   org's rows; AuditLog 'refund_policy.update' with before/after. No commit.

DEBUGGING:
# print(f"[DEBUG] refund booking={booking.id} reason={reason} days={days} pct={pct}")

OFFLINE DOCKER TEST CASES:
- 20 days out -> 100; 10 -> 50; 3 -> 25; same day -> 0; day-count in a gap -> 0.
- staff_force_cancel returns 100 even with an empty policy table.
- Overlapping rows raise PolicyValidationError and change nothing.
"""
SEEDEOF
seed_note "app/services/refund_service.py"

cat << 'SEEDEOF' > app/services/waitlist_service.py
"""
FILE PURPOSE: Waitlist join/offer/expiry/promotion (TDR v2 §3.5, §11).
Framework-agnostic; see header FLAGS 2 and 3.

PROMPT FOR LLM IMPLEMENTATION:
1. `join_waitlist(user, trek, requested_slot_count)`: trek must be 'open' and
   requested_slot_count > effective_available(trek); one live entry per
   (user, trek) else raise AlreadyWaitlistedError.
2. `leave_waitlist(entry, user)`: owner only; sets status 'expired'.
3. `effective_available(trek, for_user_id=None)`: available_slots minus the
   requested_slot_count of every LIVE 'offered' entry not owned by
   for_user_id (the offer hold, FLAG 3). Used by initiate_booking().
4. `on_slots_freed(trek)`: called after a booked-booking cancel or a Staff/
   Admin increase of available_slots. In FIFO order by created_at, offer to
   'waiting' entries whose requested_slot_count fits the REMAINING effective
   capacity (skip entries that don't fit): status 'offered', offered_at=now,
   offer_expires_at=now+WAITLIST_OFFER_WINDOW_MINUTES, then
   notification_service.send_waitlist_offer_email(entry).
5. `expire_offers() -> int`: scheduler job body (runs under tenant_bypass):
   offers past offer_expires_at -> 'expired', then re-run on_slots_freed for
   each affected trek.
6. `mark_converted(entry)`: called inside confirm_payment's winner path when
   the booker holds an 'offered' entry for that trek.

DEBUGGING:
# print(f"[DEBUG] waitlist trek={trek.id} freed={freed} offered_to={[e.user_id for e in offered]}")

OFFLINE DOCKER TEST CASES:
- Two waiting entries, one slot freed -> only the earliest is offered.
- An entry needing 3 slots is skipped when 2 are free; the next fitting entry
  is offered.
- While an offer is live, effective_available excludes its slots for others
  but not for the offered user.
- expire_offers flips an overdue offer to 'expired' and offers the next entry.
"""
SEEDEOF
seed_note "app/services/waitlist_service.py"

cat << 'SEEDEOF' > app/services/coupon_service.py
"""
FILE PURPOSE: Coupon validation, discount math, redemption, CRUD (TDR v2 §4.4;
PDR v2 §5.9). Framework-agnostic; see header FLAG 4.

PROMPT FOR LLM IMPLEMENTATION:
1. `validate_coupon(code, user, trek, base_amount) -> Coupon`: org-scoped
   lookup on the upper-cased code; must be is_active, inside valid_from/
   valid_until, used_count < max_uses (if set), base_amount >=
   min_booking_amount. Raise CouponInvalidError with a SPECIFIC reason code
   (unknown/inactive/expired/exhausted/below_minimum) — the UI shows a
   generic message for 'unknown' to avoid code enumeration.
2. `compute_discount(coupon, base_amount) -> Decimal`: percentage or flat,
   capped so base_amount - discount >= 1.00 (Razorpay minimum order).
3. `redeem_coupon(coupon, booking, user, discount)`: called only inside
   confirm_payment's winner path under lock: lock the coupon row, used_count
   += 1, insert coupon_redemptions. No commit.
4. CRUD helpers `create_coupon / update_coupon / set_active` for the admin
   routes (validate percentage <= 100, valid_until >= valid_from,
   AuditLog 'coupon.create'/'coupon.update').

DEBUGGING:
# print(f"[DEBUG] coupon {coupon.code} base={base_amount} discount={discount}")

OFFLINE DOCKER TEST CASES:
- A 100% coupon on a ₹500 booking yields payable exactly ₹1.00.
- An expired, exhausted, or below-minimum coupon each raise with the right
  reason code.
- redeem_coupon twice for the same booking raises (unique redemption).
"""
SEEDEOF
seed_note "app/services/coupon_service.py"

cat << 'SEEDEOF' > app/services/invoice_service.py
"""
FILE PURPOSE: GST invoice generation + PDF rendering (TDR v2 §11; PDR v2 §5.9).
Framework-agnostic; see header FLAG 6.

PROMPT FOR LLM IMPLEMENTATION:
1. `generate_invoice(booking) -> Invoice | None`: idempotent (UNIQUE
   booking_id). Skip with a WARNING (booking unaffected) when
   org.gst_number or GST_RATE_PERCENT is missing. Lock the organization row
   (FOR UPDATE), increment last_invoice_number, invoice_number =
   "INV-" + zero-padded sequence. Payment amount is treated as GST-INCLUSIVE:
   taxable = total / (1 + rate), gst = total - taxable (2dp, total preserved).
2. Render the PDF with WeasyPrint from email/gst_invoice_pdf.html with org
   branding + GST number + participants; write to
   instance/invoices/<org_id>/<invoice_number>.pdf and store that INTERNAL
   path in pdf_asset_url.
3. `get_invoice_pdf_bytes(invoice)`: reads the file for the authenticated
   download routes; never exposes the path.
4. Called from confirm_payment's winner path and confirm_manual_booking, then
   emailed via notification_service.send_invoice_email.

DEBUGGING:
# print(f"[DEBUG] invoice booking={booking.id} number={invoice.invoice_number} total={invoice.total_amount}")

OFFLINE DOCKER TEST CASES:
- Two bookings in one org get consecutive numbers; a second org starts at 1.
- Calling generate_invoice twice for one booking returns the same invoice.
- With GST_RATE_PERCENT blank no invoice row is created and no exception
  escapes.
- taxable + gst == total exactly for odd amounts (rounding preserved).
"""
SEEDEOF
seed_note "app/services/invoice_service.py"

cat << 'SEEDEOF' >> app/services/booking_service.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (group bookings, TDR v2 §3.2):
1. `initiate_booking(user_id, trek_id, participants, coupon_code=None)`:
   participants = list of {name, age?}; slot_count = len(participants) >= 1
   (never client-supplied); first participant is_primary_contact. Under the
   existing FOR UPDATE trek lock: reject if trek not 'open' or
   waitlist_service.effective_available(trek, user_id) < slot_count; keep the
   duplicate-active-booking guard; create ONE booking + N booking_participants
   atomically. Base amount = trek.price * slot_count; apply
   coupon_service.validate_coupon/compute_discount (coupon_id on the booking);
   payment amount passed on to payment_service.create_gateway_order(booking,
   amount). Slots are NOT decremented here (TDR §3.4).
2. If env REQUIRE_ROUTE_LINKAGE is true and the org is not route-verified,
   raise RouteNotLinkedError (online booking unavailable) — manual bookings
   are unaffected.
3. `create_manual_booking(creator, trek, target_user, participants,
   stated_amount, note=None)`: requires org.manual_booking_enabled
   (ManualBookingDisabledError otherwise); booking_channel='manual',
   status 'pending'; one payments row channel='offline', status 'initiated',
   amount=stated_amount; NO gateway call; duplicate guard applies. Slots are
   NOT touched until sign-off. AuditLog 'booking.manual_create'.
4. New errors: InvalidParticipantsError, RouteNotLinkedError,
   ManualBookingDisabledError.

DEBUGGING:
# print(f"[DEBUG] initiate_booking user={user_id} trek={trek_id} slots={slot_count} coupon={coupon_code}")

OFFLINE DOCKER TEST CASES:
- 3 participants on a trek with 2 slots raises TrekUnavailableError, no rows.
- A successful 3-person booking creates 1 booking, 3 participants, 0 slot change.
- A live waitlist offer held by another user reduces what a non-offered user can book.
- create_manual_booking with the org toggle off raises ManualBookingDisabledError.
"""
SEEDEOF
seed_note "app/services/booking_service.py (P9 addendum)"

cat << 'SEEDEOF' >> app/services/payment_service.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (the flagship engine, TDR v2 §3):
1. Slot arithmetic generalized: private helpers `_decrement_slots(trek, n)` /
   `_increment_slots(trek, n)` are the ONLY places (besides the existing
   Admin/Staff direct slot edits) that mutate available_slots. All checks use
   `available_slots >= booking.slot_count` under the FOR UPDATE trek lock.
2. confirm_payment winner path (same transaction): decrement by slot_count;
   booked/paid; payment success; if the booker holds an 'offered' waitlist
   entry for this trek -> waitlist_service.mark_converted;
   coupon_service.redeem_coupon (discount = trek.price*slot_count - payment
   amount); commit; THEN (post-commit, failures never roll back the booking):
   invoice_service.generate_invoice + email, booking-confirmed email, and
   settle_via_route(payment). Loser/failed paths unchanged from v1.
3. `route_settlement_enabled(org)`: True only if
   RAZORPAY_PLATFORM_COMMISSION_PERCENT is set AND org.razorpay_route_account_id
   exists. `settle_via_route(payment)`: if disabled log WARNING and return
   (route_transfer_status stays NULL); else create the Route transfer net of
   commission, store route_transfer_id/status 'pending'; on error set 'failed'
   + log ERROR (surfaces to reconciliation; booking untouched, TDR §3.4).
   `create_gateway_order(booking, amount)` attaches the transfer instructions
   when settlement is enabled.
4. `confirm_manual_booking(booking, signer)`: booking_channel must be
   'manual' and unsigned; lock trek; re-check open + slots >= slot_count
   (SlotUnavailableError otherwise — booking stays pending); decrement;
   booked/paid, manual_sign_off_by/at, offline payment -> 'success' with
   payment_date; invoice + confirmation email post-commit; AuditLog
   'booking.manual_sign_off'.
5. `cancel_booking(booking, actor, reason, note=None)`: reason in
   ('customer_requested','staff_force_cancel'). 'pending' -> cancelled, no
   slot change, payment initiated->failed. 'booked' -> pct =
   refund_service.calculate_refund; set cancellation_reason, cancelled_by,
   refund_percentage_applied; if pct>0 payment_status/payments.status
   'refunded' (0% leaves 'paid'); _increment_slots(slot_count); call
   initiate_refund (status-recording hook, FLAG 5); commit; then
   waitlist_service.on_slots_freed(trek) and the right email
   (force-cancel apology w/ mandatory note, or customer confirmation).
   'completed' bookings or 'completed' treks -> RefundNotAllowedError.
6. expire_stale_bookings(): add `booking_channel = 'online'` to the WHERE —
   manual pending bookings awaiting sign-off must NEVER expire (FLAG 7).

DEBUGGING:
# print(f"[DEBUG] cancel booking={booking.id} reason={reason} pct={pct} slots_back={booking.slot_count}")

OFFLINE DOCKER TEST CASES:
- Two threads confirming 2-slot bookings against 3 remaining slots: exactly
  one 'booked', other cancelled/refunded, available_slots == 1.
- Cancelling a 'booked' 3-person booking returns exactly 3 slots.
- A force-cancel always stores refund_percentage_applied=100.
- expire_stale_bookings leaves a 45-minute-old pending MANUAL booking alone.
- With commission blank, confirm_payment still books and route_transfer_status is NULL.
- A forced invoice-generation exception does not roll back the confirmed booking.
"""
SEEDEOF
seed_note "app/services/payment_service.py (P9 addendum)"

cat << 'SEEDEOF' >> app/services/notification_service.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend this module):
1. `send_waitlist_offer_email(entry)` (email/waitlist_offer.html: trek, slot
   count, claim deadline, link to the trek page).
2. `send_force_cancel_email(booking, reason_note)`
   (email/booking_force_cancelled.html: apology, the staff-written reason,
   guaranteed full-refund confirmation).
3. `send_cancellation_email(booking)` for customer cancels: refund percentage
   and amount applied.
4. `send_invoice_email(booking, invoice)`: attaches the PDF
   (invoice_service.get_invoice_pdf_bytes) — GST invoice "alongside every
   booking-status update" (PDR v2 §5.9): confirmation and cancellation emails
   attach the booking's invoice when one exists.
5. All follow the existing try/except-log-don't-raise contract. Real provider
   plumbing + consent checks arrive in P11 — keep these as thin functions.

DEBUGGING:
# print(f"[DEBUG] notification sent={template} to={recipient.email}")

OFFLINE DOCKER TEST CASES:
- Each function yields exactly one captured MailHog/mock message.
- An SMTP failure is logged, never raised.
"""
SEEDEOF
seed_note "app/services/notification_service.py (P9 addendum)"

cat << 'SEEDEOF' >> app/services/auth_service.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (inline account for manual booking):
1. `create_user_for_manual_booking(name, email, mobile_country_code,
   mobile_number)`: role 'trekker', mobile validated via validate_mobile,
   random unusable password + must_reset_password=True, terms_accepted_at
   NULL (completion gate fires at first login — Staff cannot accept terms for
   anyone). Sends a "set your password" link via the existing reset-token flow
   (no emailed plaintext password). Email unique per org; if it already
   exists return the existing user instead of creating a duplicate.

DEBUGGING:
# print(f"[DEBUG] manual-booking user email={email} created={created}")

OFFLINE DOCKER TEST CASES:
- Existing email in the org returns that user, creates nothing.
- Created user has terms_accepted_at None and needs_account_completion True.
"""
SEEDEOF
seed_note "app/services/auth_service.py (P9 addendum)"

cat << 'SEEDEOF' >> app/services/tenancy_service.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. create_organization() additionally calls refund_service.seed_default_policy
   for the new org (same transaction).

DEBUGGING:
# print(f"[DEBUG] default refund policy seeded for org={org.id}")

OFFLINE DOCKER TEST CASES:
- A freshly onboarded org has exactly 4 refund_policies rows.
"""
SEEDEOF
seed_note "app/services/tenancy_service.py (P9 addendum)"

cat << 'SEEDEOF' >> app/services/rbac_service.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (append to CAPABILITY_CATALOG):
1. 'bookings.manual_create' — "Create manual/offline bookings";
   'bookings.force_cancel' — "Force-cancel bookings on assigned treks".
   Neither is protected (the toggle and sign-off already are).

DEBUGGING:
# print(f"[DEBUG] catalog size={len(CAPABILITY_CATALOG)}")

OFFLINE DOCKER TEST CASES:
- `flask seed-rbac` adds both keys without duplicating existing ones.
"""
SEEDEOF
seed_note "app/services/rbac_service.py (P9 addendum)"

cat << 'SEEDEOF' >> app/services/trek_service.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. list_open_treks() additionally returns 'open' treks with available_slots
   == 0, each flagged `is_full`, so the waitlist CTA is reachable from the
   browse page.

DEBUGGING:
# print(f"[DEBUG] list_open_treks full={sum(1 for t in result if t.available_slots == 0)}")

OFFLINE DOCKER TEST CASES:
- A full open trek appears in the list; a full closed trek does not.
"""
SEEDEOF
seed_note "app/services/trek_service.py (P9 addendum)"
log "STEP 5 complete: services seeded"

# -----------------------------------------------------------------------------
# STEP 6: Routes + API
# -----------------------------------------------------------------------------
echo "STEP 6: Seeding routes and API..."

cat << 'SEEDEOF' > app/admin/refund_policy_routes.py
"""
FILE PURPOSE: Tenant Admin refund-tier settings (UI/UX v2 §3.3; PDR v2 §14.1
/admin/settings/refund-policy).

PROMPT FOR LLM IMPLEMENTATION:
1. GET/POST /admin/settings/refund-policy: @login_required +
   @role_required("admin"). Render the org's rows (pre-filled with the seeded
   default); POST replaces the whole table via refund_service.replace_policy;
   PolicyValidationError -> re-render with row-level errors, nothing written.
2. Note shown: ranges not covered refund 0%.

DEBUGGING:
# print(f"[DEBUG] refund policy edit rows={len(rows)}")

OFFLINE DOCKER TEST CASES:
- Staff/Trekker get 403. Overlapping ranges are rejected without a write.
"""
SEEDEOF
seed_note "app/admin/refund_policy_routes.py"

cat << 'SEEDEOF' > app/admin/coupons_routes.py
"""
FILE PURPOSE: Tenant Admin coupon management (UI/UX v2 §3.3; PDR v2 §14.1).

PROMPT FOR LLM IMPLEMENTATION:
1. @login_required + @role_required("admin") on all: GET /admin/coupons
   (list: code, type/value, validity, used/max, active), GET/POST
   /admin/coupons/new, GET/POST /admin/coupons/<uuid:id>/edit,
   POST /admin/coupons/<uuid:id>/toggle-active.
2. All writes via coupon_service; code is immutable after first redemption.

DEBUGGING:
# print(f"[DEBUG] coupons route {request.path}")

OFFLINE DOCKER TEST CASES:
- Editing the code of a redeemed coupon is rejected.
- Org B's host never lists org A's coupons.
"""
SEEDEOF
seed_note "app/admin/coupons_routes.py"

cat << 'SEEDEOF' > app/admin/manual_booking_routes.py
"""
FILE PURPOSE: Protected manual-booking controls (UI/UX v2 §3.3, §4.5; PDR v2
§5.8, §11). Every route is @protected_capability_required.

PROMPT FOR LLM IMPLEMENTATION:
1. GET/POST /admin/settings/manual-booking: toggle
   organizations.manual_booking_enabled; AuditLog 'manual_booking.toggle'.
2. GET /admin/manual-bookings: sign-off queue — manual bookings with
   status 'pending', showing trek, person, slot_count, stated amount, staff
   creator, age.
3. POST /admin/bookings/<hashid:id>/manual-sign-off: payment_service.
   confirm_manual_booking(booking, signer=current_user); SlotUnavailableError
   -> flash "slots no longer available", booking stays pending.
4. POST /admin/bookings/<hashid:id>/manual-reject (small FLAG 7 addition):
   payment_service.cancel_booking(booking, actor, 'staff_force_cancel',
   note) on a pending manual booking.
5. Sign-off and toggle are NEVER reachable through requires_capability —
   no staff role can ever hold them.

DEBUGGING:
# print(f"[DEBUG] manual route {request.path} user={current_user.id}")

OFFLINE DOCKER TEST CASES:
- A staff user with every assignable capability gets 403 on all four routes.
- Sign-off on an already signed-off booking is rejected idempotently.
"""
SEEDEOF
seed_note "app/admin/manual_booking_routes.py"

cat << 'SEEDEOF' >> app/admin/__init__.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add at the bottom: `from app.admin import refund_policy_routes,
   coupons_routes, manual_booking_routes`.

DEBUGGING:
# print("[DEBUG] admin P9 route modules imported")

OFFLINE DOCKER TEST CASES:
- create_app().url_map contains the refund-policy, coupons and manual-booking rules.
"""
SEEDEOF
seed_note "app/admin/__init__.py (P9 addendum)"

cat << 'SEEDEOF' >> app/admin/routes.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. POST /admin/bookings/<hashid:id>/force-cancel: @role_required("admin");
   required `reason` text (min 10 chars, shown to the customer); calls
   payment_service.cancel_booking(booking, current_user, 'staff_force_cancel',
   note). Illegal states flash, never 500.
2. Trek edit route: when available_slots increases, call
   waitlist_service.on_slots_freed(trek) after commit.
3. booking_detail/payment_detail contexts add slot_count, participants,
   coupon, cancellation_reason/refund %, channel, route transfer status.
4. bookings_list gains a `channel` filter (online/manual).

DEBUGGING:
# print(f"[DEBUG] admin force-cancel booking={id} note_len={len(note)}")

OFFLINE DOCKER TEST CASES:
- Force-cancelling a booked booking restores slot_count slots and stores 100%.
- Raising a full trek's available_slots from 0 to 2 offers the first waitlist entry.
"""
SEEDEOF
seed_note "app/admin/routes.py (P9 addendum)"

cat << 'SEEDEOF' >> app/staff/routes.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. GET/POST /staff/bookings/manual (endpoint staff.manual_booking_new):
   @requires_capability('bookings.manual_create'); 403 with a clear message
   when org.manual_booking_enabled is False. Form: open trek of the org
   (FLAG 7), person email (existing user -> reuse; else inline create via
   auth_service.create_user_for_manual_booking with name + country code +
   mobile), participants, stated amount, optional note ->
   booking_service.create_manual_booking. No payment widget anywhere.
2. POST /staff/bookings/<hashid:id>/force-cancel:
   @requires_capability('bookings.force_cancel') AND the booking's trek is
   assigned to this staff member; required reason; same service call as admin.
3. update-slots route: after commit, if available_slots increased call
   waitlist_service.on_slots_freed(trek).

DEBUGGING:
# print(f"[DEBUG] staff manual booking trek={trek_id} creator={current_user.id}")

OFFLINE DOCKER TEST CASES:
- Without the capability or with the toggle off -> 403.
- A new person email creates exactly one user and one pending manual booking.
- Force-cancel on another staff member's trek -> 403.
"""
SEEDEOF
seed_note "app/staff/routes.py (P9 addendum)"

cat << 'SEEDEOF' >> app/trekker/forms.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. BookingForm gains participants (FieldList of subforms: name required ≤150,
   age optional 0–120; min 1 entry, no fixed max) and coupon_code (optional,
   ≤30 chars). WaitlistForm: requested_slot_count (IntegerField min 1).
   CancelBookingForm: CSRF only (confirm).
2. slot_count is derived server-side from the participant list.

DEBUGGING:
# print(f"[DEBUG] BookingForm participants={len(form.participants.entries)} errors={form.errors}")

OFFLINE DOCKER TEST CASES:
- BookingForm.validate() is False with zero participants or a blank name.
"""
SEEDEOF
seed_note "app/trekker/forms.py (P9 addendum)"

cat << 'SEEDEOF' >> app/trekker/routes.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. POST /trekker/treks/<hashid:id>/book: pass participants + coupon_code to
   booking_service.initiate_booking, then payment_service.create_gateway_order;
   catch RouteNotLinkedError / CouponInvalidError / InvalidParticipantsError /
   existing errors with specific flashes.
2. POST /trekker/treks/<hashid:id>/waitlist (join) and
   POST /trekker/waitlist/<uuid:id>/leave; trek_detail context gains the
   user's live waitlist entry/offer.
3. POST /trekker/bookings/<hashid:id>/cancel REPLACES the 501 stub: owner
   only, reason 'customer_requested'; booking_detail shows
   refund_service.preview_refund (% and amount) in a confirm step BEFORE the
   POST. Pending bookings cancel with no refund.
4. GET /trekker/bookings/<hashid:id>/invoice: owner only; streams the PDF.
5. booking_detail context: slot_count, participants, coupon, invoice link,
   cancellation reason, refund %.

DEBUGGING:
# print(f"[DEBUG] trekker cancel booking={id} preview_pct={pct}")

OFFLINE DOCKER TEST CASES:
- Cancelling another user's booking -> 403/404.
- A booked booking 3 days out shows 25% in the preview and stores 25 on cancel.
- The invoice route for another user's booking -> 403/404.
"""
SEEDEOF
seed_note "app/trekker/routes.py (P9 addendum)"

cat << 'SEEDEOF' > app/api/route_webhook_resource.py
"""
FILE PURPOSE: HTTP adapter for Razorpay Route transfer-status events (TDR v2
§3.4, §8.2) — updates payments.route_transfer_status for reconciliation.

PROMPT FOR LLM IMPLEMENTATION:
1. `class RouteWebhookResource(Resource).post()`: verify the signature with
   RAZORPAY_ROUTE_WEBHOOK_SECRET BEFORE any DB access (400 on failure).
2. Map 'transfer.processed' -> 'processed', 'transfer.failed' -> 'failed';
   locate the Payment by route_transfer_id inside tenant_bypass(), derive the
   org from the payment, update inside tenant_context(org_id). Idempotent:
   an already-final status is a no-op. Unknown transfer -> log WARNING,
   return 200.
3. A 'failed' transfer logs ERROR and never touches booking state.
4. Always return {"received": True}, 200 for recognized signed events.

DEBUGGING:
# print(f"[DEBUG] route webhook transfer={transfer_id} status={status}")

OFFLINE DOCKER TEST CASES:
- Tampered signature -> 400, no DB writes.
- The processed fixture flips pending -> processed once; replay is a no-op.
- The failed fixture sets 'failed' and leaves the booking 'booked'.
"""
SEEDEOF
seed_note "app/api/route_webhook_resource.py"

cat << 'SEEDEOF' >> app/api/__init__.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Register RouteWebhookResource at "/payments/route-webhook" (path already
   exempt in the tenant resolver, P7).

DEBUGGING:
# print("[DEBUG] route-webhook registered")

OFFLINE DOCKER TEST CASES:
- POST /api/payments/route-webhook exists on url_map and needs no tenant host.
"""
SEEDEOF
seed_note "app/api/__init__.py (P9 addendum)"

cat << 'SEEDEOF' >> app/api/bookings_resource.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. POST body becomes {trek_id, participants:[{name, age?}], coupon_code?};
   slot_count derived server-side; new error codes COUPON_INVALID,
   ROUTE_NOT_LINKED, INVALID_PARTICIPANTS (409/422 per TDR §12 envelope).
2. PUT {"action":"cancel"} calls payment_service.cancel_booking with reason
   'customer_requested' (TDR §15's open self-cancel question is now settled
   by PDR v2 §4).

DEBUGGING:
# print(f"[DEBUG] api booking participants={len(participants)}")

OFFLINE DOCKER TEST CASES:
- POST with an empty participants list -> 422 INVALID_PARTICIPANTS.
- PUT cancel on a completed booking -> 409.
"""
SEEDEOF
seed_note "app/api/bookings_resource.py (P9 addendum)"
log "STEP 6 complete: routes and API seeded"

# -----------------------------------------------------------------------------
# STEP 7: Templates
# -----------------------------------------------------------------------------
echo "STEP 7: Seeding templates..."

cat << 'SEEDEOF' > app/templates/admin/refund_policy.html
<!--
FILE PURPOSE: Refund Policy settings (UI/UX v2 §3.3).

PROMPT FOR LLM IMPLEMENTATION:
1. Admin shell. Editable table: Min days, Max days (blank = "and above"),
   Refund %; add/remove row buttons (plain form, no JS dependency); a note
   "Day-ranges not covered refund 0%". "Reset to default" button.
2. Row-level validation errors beside the offending row.

DEBUGGING:
DEBUG: refund_policy.html rows={{ rows|length }}

OFFLINE DOCKER TEST CASES:
- Saving overlapping rows re-renders with the error and keeps user input.
-->
SEEDEOF
seed_note "app/templates/admin/refund_policy.html"

cat << 'SEEDEOF' > app/templates/admin/coupons_list.html
<!--
FILE PURPOSE: Coupons list (UI/UX v2 §3.3).

PROMPT FOR LLM IMPLEMENTATION:
1. Table: Code, Discount, Validity, Used/Max, Active toggle, Edit. "+ New Coupon".

DEBUGGING:
DEBUG: coupons_list.html count={{ coupons|length }}

OFFLINE DOCKER TEST CASES:
- An exhausted coupon displays "Exhausted" next to its usage count.
-->
SEEDEOF
seed_note "app/templates/admin/coupons_list.html"

cat << 'SEEDEOF' > app/templates/admin/coupon_form.html
<!--
FILE PURPOSE: Coupon create/edit form.

PROMPT FOR LLM IMPLEMENTATION:
1. Fields: code, type (percentage|flat), value, valid from/until, max uses,
   min booking amount, active. Code read-only once redeemed.

DEBUGGING:
DEBUG: coupon_form.html mode={{ 'edit' if coupon else 'create' }}

OFFLINE DOCKER TEST CASES:
- Percentage value > 100 is rejected server-side with an inline error.
-->
SEEDEOF
seed_note "app/templates/admin/coupon_form.html"

cat << 'SEEDEOF' > app/templates/admin/manual_booking_settings.html
<!--
FILE PURPOSE: Manual-booking on/off setting (protected; UI/UX v2 §3.3).

PROMPT FOR LLM IMPLEMENTATION:
1. A single switch card with an explanation that manual bookings skip online
   payment and require the Tenant Admin's sign-off. Visible/editable only on
   protected routes — never rendered on any staff screen.

DEBUGGING:
DEBUG: manual_booking_settings.html enabled={{ org.manual_booking_enabled }}

OFFLINE DOCKER TEST CASES:
- The toggle posts with a CSRF token and flips the stored value.
-->
SEEDEOF
seed_note "app/templates/admin/manual_booking_settings.html"

cat << 'SEEDEOF' > app/templates/admin/manual_booking_queue.html
<!--
FILE PURPOSE: Manual Booking Sign-off Queue (UI/UX v2 §3.3, §4.5).

PROMPT FOR LLM IMPLEMENTATION:
1. Table: Booking, Person, Trek, Slots, Stated Amount, Created by, Age,
   Sign-off badge (awaiting/signed off), actions "Sign off" and "Reject".
2. Sign-off failure (slots gone) shown inline; empty-state message.

DEBUGGING:
DEBUG: manual_booking_queue.html pending={{ items|length }}

OFFLINE DOCKER TEST CASES:
- Signed-off rows show no action buttons.
-->
SEEDEOF
seed_note "app/templates/admin/manual_booking_queue.html"

cat << 'SEEDEOF' > app/templates/staff/manual_booking.html
<!--
FILE PURPOSE: Staff "Create Manual Booking" form (UI/UX v2 §4.5).

PROMPT FOR LLM IMPLEMENTATION:
1. Staff shell. Trek select (open treks), person email lookup; if unknown,
   reveal name + country code + mobile fields (plain server round-trip, no JS
   requirement); participants list; stated amount; note.
2. Banner: "Payment is handled outside the platform; this booking needs Tenant
   Admin sign-off." No payment widget.

DEBUGGING:
DEBUG: manual_booking.html enabled={{ g.organization.manual_booking_enabled }}

OFFLINE DOCKER TEST CASES:
- Rendered output contains no Razorpay script or payment form.
-->
SEEDEOF
seed_note "app/templates/staff/manual_booking.html"

cat << 'SEEDEOF' > app/templates/email/gst_invoice.html
<!--
FILE PURPOSE: Email body accompanying the GST invoice PDF (PDR v2 §14.2).

PROMPT FOR LLM IMPLEMENTATION:
1. Short transactional note: invoice number, trek, total; says the PDF is
   attached. Tenant branding via the shared email structure.

DEBUGGING:
DEBUG: gst_invoice.html invoice={{ invoice.invoice_number }}

OFFLINE DOCKER TEST CASES:
- Rendered output contains the invoice number exactly once.
-->
SEEDEOF
seed_note "app/templates/email/gst_invoice.html"

cat << 'SEEDEOF' > app/templates/email/gst_invoice_pdf.html
<!--
FILE PURPOSE: The WeasyPrint PDF document template for a GST invoice (extra
file, FLAG 10). Print-safe CSS only.

PROMPT FOR LLM IMPLEMENTATION:
1. Header: organization name/logo/contact + GST number; invoice number/date;
   billed-to user. Body: trek, dates, participant names, taxable amount, GST
   rate + amount, total. Footer: tenant legal line. No external assets
   (WeasyPrint must work offline).

DEBUGGING:
DEBUG: gst_invoice_pdf.html invoice={{ invoice.invoice_number }}

OFFLINE DOCKER TEST CASES:
- Rendering to PDF in the test container produces non-empty bytes starting
  with %PDF.
-->
SEEDEOF
seed_note "app/templates/email/gst_invoice_pdf.html"

cat << 'SEEDEOF' > app/templates/email/waitlist_offer.html
<!--
FILE PURPOSE: "Waitlist Slot Available" email (PDR v2 §14.2).

PROMPT FOR LLM IMPLEMENTATION:
1. Trek name, slots offered, the claim DEADLINE (offer_expires_at in IST),
   link to the trek page. Plain, minimal.

DEBUGGING:
DEBUG: waitlist_offer.html entry={{ entry.id }}

OFFLINE DOCKER TEST CASES:
- Rendered output contains the deadline and exactly one trek link.
-->
SEEDEOF
seed_note "app/templates/email/waitlist_offer.html"

cat << 'SEEDEOF' > app/templates/email/booking_force_cancelled.html
<!--
FILE PURPOSE: "Booking Cancelled by [Company]" email (PDR v2 §14.2).

PROMPT FOR LLM IMPLEMENTATION:
1. Apologetic tone; states the company-written reason; confirms a FULL refund
   regardless of the tiered policy; links to the booking detail page.

DEBUGGING:
DEBUG: booking_force_cancelled.html booking={{ booking.id }}

OFFLINE DOCKER TEST CASES:
- Rendered output contains the staff-written reason and the words "full refund".
-->
SEEDEOF
seed_note "app/templates/email/booking_force_cancelled.html"

cat << 'SEEDEOF' >> app/templates/trekker/booking_review.html

<!--
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Participants section: repeatable name/age rows (server-rendered "Add
   participant" via a plain form round-trip; no JS dependency), first row
   marked primary contact.
2. Coupon code input; summary shows per-person price, participant count,
   discount (after server validation) and total payable.

DEBUGGING:
DEBUG: booking_review.html participants={{ form.participants|length }}

OFFLINE DOCKER TEST CASES:
- Submitting with an invalid coupon re-renders keeping participants entered.
-->
SEEDEOF
seed_note "app/templates/trekker/booking_review.html (P9 addendum)"

cat << 'SEEDEOF' >> app/templates/trekker/trek_detail.html

<!--
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. When the trek is open but full, the booking panel shows "Join Waitlist"
   (slot count input) instead of a disabled Book button; once joined show
   "You're on the waitlist"; with a live offer show the deadline and a "Book
   now" action.

DEBUGGING:
DEBUG: trek_detail.html waitlist_state={{ waitlist_state }}

OFFLINE DOCKER TEST CASES:
- A full open trek renders Join Waitlist, never a disabled Book Now.
-->
SEEDEOF
seed_note "app/templates/trekker/trek_detail.html (P9 addendum)"

cat << 'SEEDEOF' >> app/templates/trekker/browse.html

<!--
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Cards for full open treks show a "Full — waitlist" tag instead of slots left.

DEBUGGING:
DEBUG: browse.html full_count={{ treks|selectattr('is_full')|list|length }}

OFFLINE DOCKER TEST CASES:
- A full trek card carries the waitlist tag and still links to detail.
-->
SEEDEOF
seed_note "app/templates/trekker/browse.html (P9 addendum)"

cat << 'SEEDEOF' >> app/templates/trekker/booking_detail.html

<!--
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Show slot count + participant list, coupon applied, "Download GST invoice".
2. Cancel (booked or pending) opens a confirm step showing refund_service
   preview (% and amount) before the POST.
3. When cancellation_reason == 'staff_force_cancel': an apologetic notice with
   the reason and "You will receive a full refund" — visually distinct from the
   tiered-refund note shown for 'customer_requested'.

DEBUGGING:
DEBUG: booking_detail.html reason={{ booking.cancellation_reason }}

OFFLINE DOCKER TEST CASES:
- A force-cancelled booking shows the full-refund notice, never the tier copy.
-->
SEEDEOF
seed_note "app/templates/trekker/booking_detail.html (P9 addendum)"

cat << 'SEEDEOF' >> app/templates/admin/booking_detail.html

<!--
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add rows: slot count, participants, coupon, channel, cancellation reason +
   refund %, manual sign-off by/at. A "Force-cancel" form (required reason
   textarea) for booked bookings.

DEBUGGING:
DEBUG: admin booking_detail.html channel={{ booking.booking_channel }}

OFFLINE DOCKER TEST CASES:
- Force-cancel is absent for completed or already cancelled bookings.
-->
SEEDEOF
seed_note "app/templates/admin/booking_detail.html (P9 addendum)"

cat << 'SEEDEOF' >> app/templates/admin/bookings_list.html

<!--
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add a Channel column/filter (online | manual) and a Slots column.

DEBUGGING:
DEBUG: admin bookings_list.html channel_filter={{ request.args.get('channel') }}

OFFLINE DOCKER TEST CASES:
- channel=manual shows only manual bookings.
-->
SEEDEOF
seed_note "app/templates/admin/bookings_list.html (P9 addendum)"

cat << 'SEEDEOF' >> app/templates/admin/payment_detail.html

<!--
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add channel, Route transfer id and Route transfer status (badge via
   status_badge('route_transfer', ...)); NULL status renders "Not settled via
   Route".

DEBUGGING:
DEBUG: admin payment_detail.html route={{ payment.route_transfer_status }}

OFFLINE DOCKER TEST CASES:
- A NULL transfer status never renders the string "None".
-->
SEEDEOF
seed_note "app/templates/admin/payment_detail.html (P9 addendum)"

cat << 'SEEDEOF' >> app/templates/staff/participants.html

<!--
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Show slot count and participant names per booking; a "Force-cancel" action
   (reason required) rendered only when has_capability('bookings.force_cancel').

DEBUGGING:
DEBUG: staff/participants.html force_cancel={{ has_capability('bookings.force_cancel') }}

OFFLINE DOCKER TEST CASES:
- Without the capability the action is absent, not disabled.
-->
SEEDEOF
seed_note "app/templates/staff/participants.html (P9 addendum)"

cat << 'SEEDEOF' >> app/templates/shared/sidebar_admin.html

<!--
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add links: Refund Policy, Coupons, Manual Bookings (with a pending-count
   badge), Manual Booking Settings.

DEBUGGING:
DEBUG: sidebar_admin.html pending_manual={{ pending_manual_count }}

OFFLINE DOCKER TEST CASES:
- The Manual Bookings badge is absent at zero pending.
-->
SEEDEOF
seed_note "app/templates/shared/sidebar_admin.html (P9 addendum)"

cat << 'SEEDEOF' >> app/templates/shared/sidebar_staff.html

<!--
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. "Create Manual Booking" link only when has_capability('bookings.manual_create')
   AND g.organization.manual_booking_enabled.

DEBUGGING:
DEBUG: sidebar_staff.html manual_link={{ has_capability('bookings.manual_create') }}

OFFLINE DOCKER TEST CASES:
- Link absent when the org toggle is off even if the capability is held.
-->
SEEDEOF
seed_note "app/templates/shared/sidebar_staff.html (P9 addendum)"

cat << 'SEEDEOF' >> app/templates/shared/_badge_macros.html

<!--
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (UI/UX v2 §6):
1. Add domains: 'manual_signoff' (awaiting #D97706/#FDF3E3, signed_off
   #16A34A/#E7F6EC), 'waitlist' (waiting #6B7280/#EEEFF1, offered
   #0891B2/#E5F5F8, expired #DC2626/#FBE9E9, converted #16A34A/#E7F6EC),
   'cancellation_reason' (customer_requested #6B7280/#EEEFF1,
   staff_force_cancel #D97706/#FDF3E3), 'route_transfer' (pending
   #D97706/#FDF3E3, processed #16A34A/#E7F6EC, failed #DC2626/#FBE9E9).

DEBUGGING:
DEBUG: status_badge domain={{ domain }} value={{ value }}

OFFLINE DOCKER TEST CASES:
- status_badge('offered','waitlist') renders #0891B2.
-->
SEEDEOF
seed_note "app/templates/shared/_badge_macros.html (P9 addendum)"
log "STEP 7 complete: templates seeded"

# -----------------------------------------------------------------------------
# STEP 8: Fixtures
# -----------------------------------------------------------------------------
echo "STEP 8: Seeding fixtures..."

cat << 'SEEDEOF' > tests/fixtures/razorpay_route_transfer_processed.json
{
  "_seed_prompt": "FILE PURPOSE: Sample Razorpay Route 'transfer.processed' webhook body (TDR v2 §10 fixtures, zero live network). PROMPT FOR LLM IMPLEMENTATION: Replace this file's contents with a realistic event: 'event': 'transfer.processed', 'payload.transfer.entity' with 'id' (trf_... matching a route_transfer_id used in the tests), 'status': 'processed', 'amount' (paise), 'currency': 'INR', 'recipient' (acc_...). Final file must be plain valid JSON without this _seed_prompt key. DEBUGGING: log the loaded transfer id before use. OFFLINE DOCKER TEST CASES: POSTing it with a correctly computed test signature to /api/payments/route-webhook moves the matching payment's route_transfer_status from 'pending' to 'processed'."
}
SEEDEOF
seed_note "tests/fixtures/razorpay_route_transfer_processed.json"

cat << 'SEEDEOF' > tests/fixtures/razorpay_route_transfer_failed.json
{
  "_seed_prompt": "FILE PURPOSE: Sample Razorpay Route 'transfer.failed' webhook body (TDR v2 §10). PROMPT FOR LLM IMPLEMENTATION: Replace with a realistic event: 'event': 'transfer.failed', 'payload.transfer.entity' with 'id' (trf_...), 'status': 'failed', 'amount', 'currency': 'INR', 'recipient', plus a failure reason field. Final file: plain valid JSON, no _seed_prompt key. DEBUGGING: log the loaded transfer id. OFFLINE DOCKER TEST CASES: POSTing it with a valid test signature sets route_transfer_status='failed' and leaves the booking 'booked'."
}
SEEDEOF
seed_note "tests/fixtures/razorpay_route_transfer_failed.json"
log "STEP 8 complete: fixtures seeded"

# -----------------------------------------------------------------------------
# STEP 9: App factory addendum
# -----------------------------------------------------------------------------
echo "STEP 9: Appending P9 addendum to app/__init__.py..."
cat << 'SEEDEOF' >> app/__init__.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend create_app()):
1. Register the waitlist offer-expiry job against the SAME scheduler singleton
   (never call scheduler.start() again): id 'expire_waitlist_offers',
   func=waitlist_service.expire_offers, interval every 5 minutes; guard against
   double registration on repeated create_app() calls.
2. CLI `flask seed-refund-policies`: idempotent; seeds the default policy for
   every org that has none (inside tenant_bypass).
3. The Route webhook is registered through app.api (no extra wiring here).

DEBUGGING:
# print(f"[DEBUG] jobs={[j.id for j in scheduler.get_jobs()]}")

OFFLINE DOCKER TEST CASES:
- scheduler.get_job('expire_waitlist_offers') exists AND the P4/P5 jobs are
  still present.
- `flask seed-refund-policies` twice leaves exactly 4 rows per org.
"""
SEEDEOF
seed_note "app/__init__.py (P9 addendum)"
log "STEP 9 complete: app factory addendum appended"

# -----------------------------------------------------------------------------
# STEP 10: Tests
# -----------------------------------------------------------------------------
echo "STEP 10: Seeding tests..."

cat << 'SEEDEOF' > tests/unit/test_refund_service.py
"""
FILE PURPOSE: Unit tests for services/refund_service.py.

PROMPT FOR LLM IMPLEMENTATION:
1. Cover every OFFLINE TEST CASE in refund_service.py's docstring with an
   injected `today`, plus seed_default_policy idempotency and replace_policy audit rows.

DEBUGGING:
# print(f"[DEBUG] refund unit days={days} pct={pct}")

OFFLINE DOCKER TEST CASES:
- All tier boundaries (14/13/7/6/1/0 days) return the documented percentage.
"""
SEEDEOF
seed_note "tests/unit/test_refund_service.py"

cat << 'SEEDEOF' > tests/unit/test_waitlist_service.py
"""
FILE PURPOSE: Unit tests for services/waitlist_service.py.

PROMPT FOR LLM IMPLEMENTATION:
1. Cover every OFFLINE TEST CASE in waitlist_service.py's docstring, with
   notification_service mocked and time frozen for offer expiry.

DEBUGGING:
# print(f"[DEBUG] waitlist unit offered={offered_ids}")

OFFLINE DOCKER TEST CASES:
- FIFO order and fit-skipping both hold; join on a trek with room is rejected.
"""
SEEDEOF
seed_note "tests/unit/test_waitlist_service.py"

cat << 'SEEDEOF' > tests/unit/test_coupon_service.py
"""
FILE PURPOSE: Unit tests for services/coupon_service.py.

PROMPT FOR LLM IMPLEMENTATION:
1. Cover every OFFLINE TEST CASE in coupon_service.py's docstring plus
   percentage vs flat math and the max_uses overshoot behavior documented in FLAG 4.

DEBUGGING:
# print(f"[DEBUG] coupon unit code={coupon.code} discount={discount}")

OFFLINE DOCKER TEST CASES:
- Payable never falls below 1.00 for any coupon/amount combination tested.
"""
SEEDEOF
seed_note "tests/unit/test_coupon_service.py"

cat << 'SEEDEOF' > tests/unit/test_invoice_service.py
"""
FILE PURPOSE: Unit tests for services/invoice_service.py.

PROMPT FOR LLM IMPLEMENTATION:
1. Cover every OFFLINE TEST CASE in invoice_service.py's docstring; WeasyPrint
   real (offline) but file output redirected to a tmp dir.

DEBUGGING:
# print(f"[DEBUG] invoice unit number={inv.invoice_number}")

OFFLINE DOCKER TEST CASES:
- Per-org numbering is independent and gap-free across concurrent calls.
"""
SEEDEOF
seed_note "tests/unit/test_invoice_service.py"

cat << 'SEEDEOF' >> tests/integration/test_booking_race.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (group-booking variant, TDR v2 §3.2):
1. Seed a trek with available_slots=3 and two bookings of slot_count=2 each
   (both pending, both passed initiation). Two threads confirm simultaneously.
2. Assert: exactly one booking 'booked' (2 slots), the other cancelled/
   refunded, available_slots == 1 — neither alone exceeds capacity but
   together they do.
3. A second case: slot_count=1 and slot_count=2 vs 3 slots -> both succeed,
   available_slots == 0, never negative.

DEBUGGING:
# print(f"[DEBUG] group race winner={winner.id} final_slots={trek.available_slots}")

OFFLINE DOCKER TEST CASES:
- Passes deterministically across 20 consecutive Postgres runs.
"""
SEEDEOF
seed_note "tests/integration/test_booking_race.py (P9 addendum)"

cat << 'SEEDEOF' > tests/integration/test_cancellation_flows.py
"""
FILE PURPOSE: Integration tests for self-cancel and force-cancel paths.

PROMPT FOR LLM IMPLEMENTATION:
1. Self-cancel at each refund tier (preview == stored %); pending cancel (no
   slot change, payment failed); force-cancel by Admin and by Staff (own trek
   only) -> 100%, apology email, slots restored; completed booking rejected;
   waitlist offer fired after a freeing cancel; invoice still downloadable.

DEBUGGING:
# print(f"[DEBUG] cancel flow booking={booking.id} pct={booking.refund_percentage_applied}")

OFFLINE DOCKER TEST CASES:
- Slots restored always equal the booking's slot_count.
"""
SEEDEOF
seed_note "tests/integration/test_cancellation_flows.py"

cat << 'SEEDEOF' > tests/integration/test_manual_booking.py
"""
FILE PURPOSE: Integration tests for manual/offline booking end to end.

PROMPT FOR LLM IMPLEMENTATION:
1. Toggle off -> 403; toggle on -> create (existing + new person); queue lists
   it; sign-off books it, decrements slots, creates an invoice; slot vanished
   before sign-off -> stays pending; reject path; stale-expiry job skips it;
   no staff role can reach toggle/sign-off/reject; cross-tenant isolation.

DEBUGGING:
# print(f"[DEBUG] manual flow booking={booking.id} status={booking.status}")

OFFLINE DOCKER TEST CASES:
- No payment gateway call is ever made on this flow (mock asserts zero calls).
"""
SEEDEOF
seed_note "tests/integration/test_manual_booking.py"

cat << 'SEEDEOF' > tests/integration/test_waitlist_flow.py
"""
FILE PURPOSE: Integration tests for waitlist join -> offer -> convert/expire.

PROMPT FOR LLM IMPLEMENTATION:
1. Full trek join; cancel frees a slot -> earliest offered + email; non-offered
   user blocked during the live offer; offered user books and entry becomes
   'converted'; overdue offer expires via the job and the next entry is offered;
   Staff slot increase also triggers offers.

DEBUGGING:
# print(f"[DEBUG] waitlist flow entry={entry.id} status={entry.status}")

OFFLINE DOCKER TEST CASES:
- Every state transition lands in the documented status with zero live calls.
"""
SEEDEOF
seed_note "tests/integration/test_waitlist_flow.py"

cat << 'SEEDEOF' > tests/integration/test_route_webhook.py
"""
FILE PURPOSE: Integration tests for Route settlement + webhook.

PROMPT FOR LLM IMPLEMENTATION:
1. Commission blank -> booking confirms, route status NULL, WARNING logged.
   Commission set + linked org -> transfer created 'pending'; processed/failed
   fixtures update status (signature checked first); replays are no-ops; a
   failed transfer never changes booking state. Razorpay fully mocked.

DEBUGGING:
# print(f"[DEBUG] route webhook status={resp.status_code} payment={payment.route_transfer_status}")

OFFLINE DOCKER TEST CASES:
- Tampered signature -> 400 with zero DB writes.
"""
SEEDEOF
seed_note "tests/integration/test_route_webhook.py"

cat << 'SEEDEOF' >> tests/isolation/test_tenant_scoping.py

"""
P9 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add model factories for booking_participants, refund_policies, coupons,
   coupon_redemptions, invoices, waitlist_entries so the auto-discovery check
   stays green; add HTTP isolation cases for coupons, manual-booking queue,
   invoice download and waitlist routes.

DEBUGGING:
# print(f"[DEBUG] isolation P9 model={model.__name__}")

OFFLINE DOCKER TEST CASES:
- Every new tenant-scoped model passes read/get/update/delete isolation.
"""
SEEDEOF
seed_note "tests/isolation/test_tenant_scoping.py (P9 addendum)"
log "STEP 10 complete: tests seeded"

# -----------------------------------------------------------------------------
# STEP 11: Commits (left for after code-implementer fills in real logic)
# -----------------------------------------------------------------------------
#git add app/models app/services/booking_service.py app/services/payment_service.py
#git commit -m "feat(booking): postgres row-locking and group-booking support"
#git add app/services/waitlist_service.py
#git commit -m "feat(booking): waitlist auto-promotion"
#git add app/services/refund_service.py app/services/coupon_service.py app/services/invoice_service.py
#git commit -m "feat(payments): per-tenant refund policy and coupon codes"
#git add app/admin/manual_booking_routes.py app/staff
#git commit -m "feat(payments): manual/offline booking with tenant-admin sign-off"
#git add app/api/route_webhook_resource.py
#git commit -m "feat(payments): razorpay route split settlement"
#git add tests
#git commit -m "test(booking): postgres concurrency suite incl. group-booking race"
#git push -u origin "$BRANCH_NAME"

# -----------------------------------------------------------------------------
# STEP 12: Summary
# -----------------------------------------------------------------------------
echo ""
echo "====================================================="
echo "Milestone P9 (v2) Seeded Successfully!"
echo "-----------------------------------------------------"
echo "Seed files created this run:"
for f in "${SEED_FILES[@]}"; do echo "  - $f"; done
echo "-----------------------------------------------------"
echo "STILL BLOCKED (decide before Route goes live):"
echo "  - Platform commission model (RAZORPAY_PLATFORM_COMMISSION_PERCENT)"
echo "  - Payment Aggregator compliance sign-off"
echo "-----------------------------------------------------"
echo "NEXT STEPS:"
echo "1. Copy new keys from .env.example into .env; docker compose build"
echo "   (Dockerfile changed)."
echo "2. Feed these seeds to the code-implementer skill."
echo "3. flask seed-rbac && flask seed-refund-policies"
echo "4. docker compose run --rm test   (incl. isolation + group race)"
echo "5. Open a PR: $BRANCH_NAME -> main once tests pass."
echo "6. Execute P10.sh (Trek Content & Discovery)."
echo "====================================================="
log "===== P9.sh (v2) completed successfully ====="

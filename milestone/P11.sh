#!/usr/bin/env bash
###############################################################################
# P11.sh — v2 MILESTONE 11: Communication Platform
# TMA Platform — Multi-Tenant Trekking Management SaaS
#
# Covers (Master Project Blueprint v2 §3, phase P11):
#   - Production WhatsApp (Meta Cloud API/BSP), email provider, SMS
#   - Consent model (transactional vs marketing, per channel) enforced IN THE
#     SEND PATH + per-user notification preferences
#   - Campaign composer: merge fields, audience-segment builder, mandatory
#     test-send, consent-filtered real send
#   - Point-of-contact reveal logic
#
# Depends on: P7 (mobile + tenancy), P8 (capability-gated composer). P9/P10 are
# not hard dependencies — branch base falls back P10 -> P9 -> P8.
# Never writes real application logic — only git/dir/config setup + PROMPT seeds.
#
# FLAGGED DECISIONS:
#  1. PROVIDERS: seeded as adapters behind notification_service (smtp | sendgrid
#     | ses; twilio; meta_cloud_api). Meta Cloud API vs a BSP is undecided
#     (default meta_cloud_api). EMAIL_PROVIDER defaults to `smtp` (MailHog) for
#     dev/test — TDR v2 §16's example shows sendgrid. All providers are mocked
#     in tests (offline-first rule).
#  2. WHATSAPP TEMPLATES: business-initiated WhatsApp needs Meta-APPROVED
#     templates, so a free-text campaign body cannot be sent as-is. Seeded:
#     message_campaigns.provider_template_name (NOT in TDR v2; required when
#     channel='whatsapp') and a registry mapping transactional template keys to
#     approved template names. Unmapped -> WhatsApp skipped + logged.
#  3. ONE WHATSAPP NUMBER: TDR §16 has a single WHATSAPP_PHONE_NUMBER_ID, i.e.
#     shared by all tenants — per-tenant sender identity is undecided. A STOP
#     reply revokes marketing consent for EVERY user with that number (all
#     tenants) — privacy-conservative.
#  4. INDIA SMS needs TRAI DLT registration (sender ID + template IDs); the docs
#     don't mention it. The SMS adapter has a placeholder for DLT template ids —
#     undecided, needs your compliance input.
#  5. CONSENT CAPTURE: registration gains opt-ins — WhatsApp/SMS transactional
#     (explicit checkboxes) and marketing per channel (unchecked). Email
#     transactional is implied by account creation and recorded. Wording is the
#     client's legal team's; engineering owns the timestamped record. Legacy
#     users: `flask backfill-consents` grants transactional EMAIL only.
#  6. SECURITY CARVE-OUT: password-reset / OTP / forced-change emails bypass
#     preferences and consent (never any marketing content in them).
#  7. MERGE FIELDS use whitelisted placeholder substitution — user-authored
#     text is NEVER evaluated as a Jinja template (template-injection risk).
#  8. TEST-SEND is mandatory: send is only possible when status == 'tested'; any
#     edit to body/subject/filter/channel resets it to 'draft'. The real send
#     re-resolves audience + consent at send time (TDR v2 §6). Test sends are
#     rate-limited and never create recipient rows.
#  9. POINT OF CONTACT: TDR v2 has only treks.assigned_staff_id — no multi-staff
#     "on-ground team" table — so the "full team revealed closer to the date"
#     half CANNOT be built without a new table. Seeded POC-only (the assigned
#     staff); tell me if you want trek_team_members added.
# 10. SMS-OTP MOBILE VERIFICATION (TDR v2 §8.1) is NOT seeded: PDR v2 §5.5
#     requires only validation. Confirm if verification-at-signup is wanted.
# 11. Marketing emails get a signed one-click unsubscribe link + List-Unsubscribe
#     header (provider/best-practice requirement; small addition beyond docs).
# 12. Columns beyond TDR v2: message_campaigns.provider_template_name,
#     message_campaign_recipients.provider_message_id (needed for receipts).
# 13. Extra files beyond the Blueprint tree: api/whatsapp_webhook_resource.py,
#     admin/campaign_detail.html, email/campaign_wrapper.html,
#     trekker/unsubscribe.html.
# 14. Single capability 'campaigns.compose'; Tenant Admin sees all campaigns,
#     staff only their own. Delivery receipts exist only for WhatsApp (webhook);
#     email/SMS record sent/failed from the synchronous provider response.
#
# USAGE:
#   cd ~/Desktop/t
#   bash ./docs/P11.sh
###############################################################################

set -euo pipefail

BRANCH_NAME="phase/P11-communication-platform"
LOG_FILE="./docs/p11_v2_milestone.log"

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
log "===== P11.sh (v2) started — Communication Platform ====="

# -----------------------------------------------------------------------------
# STEP 0: Pre-flight
# -----------------------------------------------------------------------------
echo "STEP 0: Pre-flight checks..."
[ -d ".git" ] || { echo "ERROR: no .git found."; exit 1; }
for f in app/tenancy/scoping.py app/services/rbac_service.py app/services/notification_service.py \
         app/services/trek_service.py app/services/auth_service.py app/public/routes.py \
         app/tenancy/resolver.py; do
  [ -f "$f" ] || { echo "ERROR: $f missing — run P7.sh/P8.sh first."; exit 1; }
done
log "STEP 0 complete: pre-flight checks passed"

# -----------------------------------------------------------------------------
# STEP 1: Branch (base: P10 -> P9 -> P8)
# -----------------------------------------------------------------------------
echo "STEP 1: Checking out milestone branch..."
BASE_BRANCH=""
for b in phase/P10-trek-content-discovery phase/P9-booking-money-engine phase/P8-identity-access-governance; do
  if git show-ref --verify --quiet "refs/heads/$b"; then BASE_BRANCH="$b"; break; fi
done
[ -n "$BASE_BRANCH" ] || { echo "ERROR: none of the P10/P9/P8 branches found."; exit 1; }
checkout_phase_branch "$BASE_BRANCH" "$BRANCH_NAME"
log "STEP 1 complete: on $BRANCH_NAME (base: $BASE_BRANCH)"

# -----------------------------------------------------------------------------
# STEP 2: Directories + infra/config
# -----------------------------------------------------------------------------
echo "STEP 2: Creating directories and config..."
mkdir -p tests/fixtures tests/unit tests/integration

ensure_req "sendgrid>=6.10,<7.0"
ensure_req "twilio>=8.0,<10.0"
ensure_req "requests>=2.31,<3.0"
seed_note "requirements.txt (P11: sendgrid, twilio, requests)"

if ! grep -q "TMA Platform v2 (P11)" .env.example; then
cat << 'SEEDEOF' >> .env.example

# --- TMA Platform v2 (P11) ---
EMAIL_PROVIDER=smtp                  # smtp (MailHog, dev/test) | sendgrid | ses
SENDGRID_API_KEY=
SMS_PROVIDER=twilio
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
WHATSAPP_PROVIDER=meta_cloud_api
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_BUSINESS_ACCOUNT_ID=
WHATSAPP_WEBHOOK_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=
SEEDEOF
fi
seed_note ".env.example"
echo "  !! Copy the new keys from .env.example into your real .env."
log "STEP 2 complete: directories, requirements, .env.example"

# -----------------------------------------------------------------------------
# STEP 3: Models
# -----------------------------------------------------------------------------
echo "STEP 3: Seeding models..."

cat << 'SEEDEOF' > app/models/notification_consent.py
"""
FILE PURPOSE: The `notification_consents` table — timestamped, per-user,
per-channel, per-type consent records (TDR v2 §4.5; PDR v2 §5.11).

PROMPT FOR LLM IMPLEMENTATION:
1. Inherit TenantScopedMixin. Columns: id (UUID PK), user_id (Integer FK
   users.id NOT NULL), channel (String(10) NOT NULL, CHECK IN
   ('email','whatsapp','sms')), consent_type (String(15) NOT NULL, CHECK IN
   ('transactional','marketing')), granted_at (DateTime(timezone=True) NOT
   NULL), revoked_at (DateTime(timezone=True) null).
   UniqueConstraint(user_id, channel, consent_type).
2. Property `is_active`: revoked_at is None. Grant/revoke is done by flipping
   these two columns on the single row (history of changes goes to AuditLog,
   not extra rows).

DEBUGGING:
# print(f"[DEBUG] consent user={self.user_id} {self.channel}/{self.consent_type} active={self.is_active}")

OFFLINE DOCKER TEST CASES:
- A second row for the same (user, channel, type) raises IntegrityError.
- channel='telegram' or consent_type='promo' raises IntegrityError.
"""
SEEDEOF
seed_note "app/models/notification_consent.py"

cat << 'SEEDEOF' > app/models/notification_preference.py
"""
FILE PURPOSE: The `notification_preferences` table — per-user per-channel
on/off switch (TDR v2 §4.5).

PROMPT FOR LLM IMPLEMENTATION:
1. Inherit TenantScopedMixin. Columns: id (UUID PK), user_id (Integer FK),
   channel (String(10), CHECK IN ('email','whatsapp','sms')), is_enabled
   (Boolean NOT NULL default True). UniqueConstraint(user_id, channel).
2. Absence of a row means enabled (default True) — the dispatcher must treat
   a missing row as enabled, never as disabled.

DEBUGGING:
# print(f"[DEBUG] preference user={self.user_id} {self.channel} enabled={self.is_enabled}")

OFFLINE DOCKER TEST CASES:
- Duplicate (user, channel) raises IntegrityError.
"""
SEEDEOF
seed_note "app/models/notification_preference.py"

cat << 'SEEDEOF' > app/models/message_campaign.py
"""
FILE PURPOSE: The `message_campaigns` table — a composed message + audience
(TDR v2 §4.5).

PROMPT FOR LLM IMPLEMENTATION:
1. Inherit TenantScopedMixin. Columns: id (UUID PK), created_by (Integer FK
   users.id NOT NULL), channel (String(10) NOT NULL, CHECK IN
   ('email','whatsapp','sms')), subject (String(200) null — email only),
   template_body (Text NOT NULL — contains merge-field placeholders),
   audience_filter (JSONB NOT NULL — serialized segment criteria), status
   (String(10) NOT NULL default 'draft', CHECK IN ('draft','tested','sent')),
   test_sent_to_email / test_sent_to_mobile (String null), sent_at
   (DateTime(timezone=True) null), recipient_count (Integer null),
   provider_template_name (String(100) null — header FLAG 2/12; required by
   campaign_service when channel='whatsapp'), created_at.
2. Relationship `recipients`.

DEBUGGING:
# print(f"[DEBUG] campaign {self.id} channel={self.channel} status={self.status}")

OFFLINE DOCKER TEST CASES:
- status='queued' raises IntegrityError; audience_filter round-trips as JSON.
"""
SEEDEOF
seed_note "app/models/message_campaign.py"

cat << 'SEEDEOF' > app/models/message_campaign_recipient.py
"""
FILE PURPOSE: The `message_campaign_recipients` table — one row per REAL
recipient of a sent campaign (TDR v2 §4.5). Test sends never create rows.

PROMPT FOR LLM IMPLEMENTATION:
1. Inherit TenantScopedMixin. Columns: id (UUID PK), campaign_id (UUID FK
   NOT NULL, ON DELETE CASCADE), user_id (Integer FK users.id NOT NULL),
   delivery_status (String(20) NOT NULL — queued|sent|delivered|read|failed),
   sent_at (DateTime(timezone=True) null), provider_message_id (String(100)
   null, indexed — FLAG 12, links WhatsApp receipts back to the row).
   UniqueConstraint(campaign_id, user_id).

DEBUGGING:
# print(f"[DEBUG] recipient campaign={self.campaign_id} user={self.user_id} status={self.delivery_status}")

OFFLINE DOCKER TEST CASES:
- The same user twice in one campaign raises IntegrityError.
- Deleting a campaign deletes its recipient rows.
"""
SEEDEOF
seed_note "app/models/message_campaign_recipient.py"

cat << 'SEEDEOF' >> app/models/__init__.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Import and add to __all__: NotificationConsent, NotificationPreference,
   MessageCampaign, MessageCampaignRecipient.

DEBUGGING:
# print(f"[DEBUG] models package exports {len(__all__)}")

OFFLINE DOCKER TEST CASES:
- db.metadata.tables now also contains notification_consents,
  notification_preferences, message_campaigns, message_campaign_recipients.
"""
SEEDEOF
seed_note "app/models/__init__.py (P11 addendum)"
log "STEP 3 complete: models seeded"

# -----------------------------------------------------------------------------
# STEP 4: Services
# -----------------------------------------------------------------------------
echo "STEP 4: Seeding services..."

cat << 'SEEDEOF' > app/services/audience_service.py
"""
FILE PURPOSE: Audience-segment filter validation + resolution (TDR v2 §11;
PDR v2 §5.11). Framework-agnostic, org-scoped by the tenant filter.

PROMPT FOR LLM IMPLEMENTATION:
1. `validate_filter(filter_json) -> dict`: WHITELISTED keys only — roles
   (subset of trekker|staff), booking_statuses (subset of the four booking
   statuses), booked_trek_ids / attended_trek_ids (lists of opaque ids decoded
   via id_codec), signup_from / signup_to (ISO dates), is_active (bool),
   has_any_booking (bool). Unknown keys or bad types -> InvalidAudienceFilter.
   Never builds SQL from raw strings.
2. `resolve_segment(filter_json, channel, consent_type) -> list[User]`: apply
   the validated filter, then ALWAYS intersect with: is_active, an ACTIVE
   notification_consents row for (channel, consent_type), notification_
   preference enabled (missing row = enabled), and a reachable address
   (email present; for sms/whatsapp a real mobile — never the legacy 'UNSET'
   sentinel). The consent intersection is not optional and not caller-bypassable.
3. `estimate_count(filter_json, channel, consent_type) -> int` for the composer.

DEBUGGING:
# print(f"[DEBUG] audience filter={filter_json} channel={channel} -> {len(users)} users")

OFFLINE DOCKER TEST CASES:
- A filter matching 10 users where 4 lack marketing consent resolves 6 for a
  marketing send; a transactional-only consenter is excluded.
- A filter with an unknown key raises InvalidAudienceFilter.
- A user with mobile 'UNSET' is excluded from sms/whatsapp, kept for email.
"""
SEEDEOF
seed_note "app/services/audience_service.py"

cat << 'SEEDEOF' > app/services/campaign_service.py
"""
FILE PURPOSE: Campaign lifecycle — merge-field rendering, draft/test/send
(TDR v2 §5, §6; PDR v2 §5.11, §4.6). Framework-agnostic.

PROMPT FOR LLM IMPLEMENTATION:
1. `render_merge_fields(template_body, context) -> str`: replace ONLY the
   whitelisted placeholders {first_name}, {name}, {organization_name},
   {trek_name}, {trek_start_date}, {booking_id} by plain substitution (unknown
   placeholder -> left visible + reported, never an error at send). NEVER pass
   author text through Jinja/format-string evaluation (FLAG 7).
2. `create_campaign / update_campaign(campaign, actor, ...)`: validates
   channel, subject (email only), body, audience via audience_service; for
   channel 'whatsapp' require provider_template_name. ANY update to body,
   subject, filter, channel or template name resets status to 'draft' and
   clears test_sent_to_*. AuditLog 'campaign.create' / 'campaign.update'.
3. `test_send(campaign, actor, test_email, test_mobile)`: renders against the
   composer's OWN data (or a sample record), delivers ONLY to the given test
   destination through notification_service, creates NO recipient rows, sets
   status 'tested' + test_sent_to_*. AuditLog 'campaign.test_send'.
4. `send_campaign(campaign, actor)`: requires status == 'tested'
   (CampaignNotTestedError). Re-resolves the audience + consent AT SEND TIME via
   audience_service.resolve_segment with consent_type 'marketing'; creates one
   recipient row per user, dispatches via notification_service.dispatch
   (marketing), records provider_message_id/delivery_status, sets status 'sent',
   sent_at, recipient_count (= number actually dispatched). One recipient's
   failure never aborts the rest. Idempotent: a second call on 'sent' raises
   CampaignAlreadySentError. AuditLog 'campaign.send'.
5. Scoping: Tenant Admin may act on any campaign of the org; staff only on
   campaigns where created_by == their user id.

DEBUGGING:
# print(f"[DEBUG] campaign {campaign.id} action={action} recipients={count}")

OFFLINE DOCKER TEST CASES:
- A body containing "{{ 7*7 }}" or "{__class__}" is sent literally, never evaluated.
- Editing a 'tested' campaign sets it back to 'draft'; send then raises
  CampaignNotTestedError.
- test_send creates zero recipient rows and contacts only the test destination.
- A user who revoked marketing consent between draft and send is not messaged.
- recipient_count equals the number of recipient rows with a dispatch attempt.
"""
SEEDEOF
seed_note "app/services/campaign_service.py"

cat << 'SEEDEOF' >> app/services/notification_service.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (production comms, TDR v2 §8.1):
1. PROVIDER ADAPTERS as private functions behind a provider registry selected
   by env: email (smtp via Flask-Mail | sendgrid | ses), sms (twilio, with a
   placeholder parameter for India DLT template ids — FLAG 4), whatsapp
   (meta_cloud_api over `requests`). Unknown provider name or missing
   credentials in a non-debug app -> fail fast at startup. Every adapter
   returns (status, provider_message_id) and never raises to callers.
2. `dispatch(user, channel, category, template_key, context, critical=False)`
   is the SINGLE send path. Gate order: (a) critical=True (password reset/OTP/
   forced-change; email only; FLAG 6) skips the gates below; (b) user active
   and address reachable (never the 'UNSET' mobile); (c) an ACTIVE
   notification_consents row for (channel, category); (d) notification_
   preference enabled (missing row = enabled). Failing a gate returns
   'skipped' with a DEBUG log — never an error.
3. WhatsApp business-initiated sends use ONLY approved templates: transactional
   keys map through a module registry (template_key -> approved template name
   + ordered variables); campaigns use message_campaigns.provider_template_name.
   Unmapped key -> 'skipped' + WARNING (FLAG 2).
4. Marketing emails render inside email/campaign_wrapper.html with a signed
   one-click unsubscribe URL and a List-Unsubscribe / List-Unsubscribe-Post
   header (FLAG 11).
5. Re-route the existing senders (OTP reset, staff approved, booking
   confirmed, ticket response, waitlist offer, cancellation, invoice, review
   request) through dispatch with category 'transactional'; fan out to every
   channel the user consented to and enabled; OTP reset stays critical.
   Their public signatures do not change.
6. Provider SDK calls are isolated so tests monkeypatch ONE seam per channel.

DEBUGGING:
# print(f"[DEBUG] dispatch user={user.id} channel={channel} category={category} -> {status}")

OFFLINE DOCKER TEST CASES:
- A marketing dispatch to a transactional-only consenter returns 'skipped'
  and the provider mock is never called.
- A critical password-reset email is sent even with email preference disabled.
- A disabled WhatsApp preference blocks WhatsApp but not email for the same user.
- An unmapped WhatsApp template key is skipped, not sent as free text.
- With EMAIL_PROVIDER=smtp the message lands in MailHog/mocked Flask-Mail only.
"""
SEEDEOF
seed_note "app/services/notification_service.py (P11 addendum)"

cat << 'SEEDEOF' >> app/services/auth_service.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (consent capture, FLAG 5):
1. `record_consent(user, channel, consent_type, granted: bool, source)`:
   upserts the single notification_consents row (granted -> granted_at=now,
   revoked_at NULL; revoked -> revoked_at=now). AuditLog 'consent.grant' /
   'consent.revoke' with the source ('registration','preferences',
   'unsubscribe_link','whatsapp_stop'). No commit.
2. register_user(): after creating the user also record transactional EMAIL
   consent (implied by account creation) plus the opt-ins submitted on the
   registration form (WhatsApp/SMS transactional, marketing per channel).
3. `backfill_consents()` for legacy users: transactional EMAIL only; never
   WhatsApp/SMS/marketing. Idempotent; runs under tenant_bypass (CLI).

DEBUGGING:
# print(f"[DEBUG] consent {channel}/{consent_type} granted={granted} user={user.id} source={source}")

OFFLINE DOCKER TEST CASES:
- Registering with no boxes ticked leaves exactly one active consent
  (email/transactional).
- Registering with WhatsApp-marketing ticked records that row with a timestamp.
- backfill_consents twice creates one email/transactional row per legacy user.
"""
SEEDEOF
seed_note "app/services/auth_service.py (P11 addendum)"

cat << 'SEEDEOF' >> app/services/trek_service.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (point of contact, FLAG 9):
1. `get_visible_contacts(trek, booking) -> dict`: for a trekker with a
   'booked' booking on the trek, returns exactly ONE point of contact — the
   trek's assigned staff (name + staff_profiles.contact_details); no contact
   before the booking is 'booked'. The "full on-ground team closer to the
   date" half is NOT implemented (no team table in TDR v2) — leave a clearly
   named extension point and a docstring note.

DEBUGGING:
# print(f"[DEBUG] contacts trek={trek.id} booking={booking.id} -> {list(result)}")

OFFLINE DOCKER TEST CASES:
- A pending booking sees no contact; a booked one sees exactly the assigned staff.
- A trek with no assigned staff returns an empty contact, not an error.
"""
SEEDEOF
seed_note "app/services/trek_service.py (P11 addendum)"

cat << 'SEEDEOF' >> app/services/rbac_service.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (append to CAPABILITY_CATALOG):
1. 'campaigns.compose' — "Compose, test-send and send campaigns". Not protected.

DEBUGGING:
# print(f"[DEBUG] catalog size={len(CAPABILITY_CATALOG)}")

OFFLINE DOCKER TEST CASES:
- `flask seed-rbac` adds the key without duplicating existing ones.
"""
SEEDEOF
seed_note "app/services/rbac_service.py (P11 addendum)"
log "STEP 4 complete: services seeded"

# -----------------------------------------------------------------------------
# STEP 5: Forms, routes, API
# -----------------------------------------------------------------------------
echo "STEP 5: Seeding forms, routes and API..."

cat << 'SEEDEOF' >> app/admin/forms.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. CampaignForm: channel (SelectField email|whatsapp|sms), subject (≤200,
   required only for email), template_body (TextArea, required),
   provider_template_name (≤100, required only for whatsapp, validated in the
   route since it depends on channel), plus the audience-builder fields: roles
   (multi-select), booking_statuses (multi-select), trek filters
   (multi-select of this org's treks, opaque ids), signup_from/signup_to,
   is_active, has_any_booking. TestSendForm: test_email, test_mobile
   (country code + number).
2. The form assembles audience_filter and defers all validation to
   audience_service/campaign_service.

DEBUGGING:
# print(f"[DEBUG] CampaignForm errors: {form.errors}")

OFFLINE DOCKER TEST CASES:
- CampaignForm.validate() is False for channel='email' with a blank subject.
"""
SEEDEOF
seed_note "app/admin/forms.py (P11 addendum)"

cat << 'SEEDEOF' > app/admin/campaigns_routes.py
"""
FILE PURPOSE: Campaign Composer routes, shared by Tenant Admin and permitted
Staff (UI/UX v2 §3.3, §3.4, §4.6; PDR v2 §14.1). Attached to admin_bp; shell
chosen by role (same pattern as the shared tickets screens).

PROMPT FOR LLM IMPLEMENTATION:
1. Every route: @login_required + @requires_capability('campaigns.compose')
   (NOT @role_required("admin") — capable staff must pass). Staff only see/act
   on campaigns they created; Tenant Admin sees all (FLAG 14).
2. GET /admin/campaigns (list + status badge); GET/POST /admin/campaigns/new;
   GET/POST /admin/campaigns/<uuid:id>/edit (draft/tested only; editing resets
   to draft with a visible notice); GET /admin/campaigns/<uuid:id> (detail:
   content, audience estimate, delivery stats per status).
3. POST /admin/campaigns/<uuid:id>/test-send: `@limiter.limit("10/hour")`;
   TestSendForm; campaign_service.test_send; flash the destination used.
4. POST /admin/campaigns/<uuid:id>/send: `@limiter.limit("5/hour")`; requires
   status 'tested' (button absent otherwise); shows a confirm step with the
   consent-filtered recipient estimate; campaign_service.send_campaign.
5. The audience estimate always reflects consent filtering for the campaign's
   channel with consent_type 'marketing'.

DEBUGGING:
# print(f"[DEBUG] campaigns route {request.path} user={current_user.id} role={current_user.role}")

OFFLINE DOCKER TEST CASES:
- Staff without the capability get 403; Tenant Admin passes.
- Staff cannot open another user's campaign (404/403).
- The Send action is unavailable until a test send has happened.
"""
SEEDEOF
seed_note "app/admin/campaigns_routes.py"

cat << 'SEEDEOF' >> app/admin/__init__.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add `from app.admin import campaigns_routes` at the bottom.

DEBUGGING:
# print("[DEBUG] admin campaigns_routes imported")

OFFLINE DOCKER TEST CASES:
- create_app().url_map contains the admin.campaign* rules.
"""
SEEDEOF
seed_note "app/admin/__init__.py (P11 addendum)"

cat << 'SEEDEOF' >> app/trekker/forms.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. NotificationPreferencesForm: per channel (email/whatsapp/sms) an
   `enabled` BooleanField, a `transactional` and a `marketing` consent
   BooleanField. CSRF protected.

DEBUGGING:
# print(f"[DEBUG] NotificationPreferencesForm data={form.data}")

OFFLINE DOCKER TEST CASES:
- The form exposes exactly 9 boolean fields.
"""
SEEDEOF
seed_note "app/trekker/forms.py (P11 addendum)"

cat << 'SEEDEOF' >> app/trekker/routes.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. GET/POST /trekker/notification-preferences (endpoint
   trekker.notification_preferences; PDR v2 §14.1 GET/PUT): shows current
   consents + enabled flags; POST updates notification_preferences and calls
   auth_service.record_consent(source='preferences') for each change.
2. Email transactional consent can be viewed but its revocation shows a note
   that security emails (password reset) are always sent (FLAG 6).
3. booking_detail context adds `contact = trek_service.get_visible_contacts(...)`
   (point of contact), rendered by the template addendum.

DEBUGGING:
# print(f"[DEBUG] preferences user={current_user.id} changes={changes}")

OFFLINE DOCKER TEST CASES:
- Revoking WhatsApp marketing sets revoked_at and logs one audit row.
- A user cannot view or edit another user's preferences.
"""
SEEDEOF
seed_note "app/trekker/routes.py (P11 addendum)"

cat << 'SEEDEOF' >> app/auth/forms.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. RegisterForm gains optional BooleanFields (all default UNCHECKED):
   whatsapp_transactional, sms_transactional, marketing_email,
   marketing_whatsapp, marketing_sms. Wording supplied by the client's legal
   team via the tenant's legal text; field labels are placeholders.

DEBUGGING:
# print(f"[DEBUG] consent fields={[f for f in form.data if f.startswith(('marketing','whatsapp','sms'))]}")

OFFLINE DOCKER TEST CASES:
- Submitting the form with none of the boxes ticked validates.
"""
SEEDEOF
seed_note "app/auth/forms.py (P11 addendum)"

cat << 'SEEDEOF' >> app/auth/routes.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. POST /register passes the new opt-in flags to auth_service.register_user so
   consents are recorded in the same transaction as the user.
2. The complete-account step (P7) does NOT silently grant any consent.

DEBUGGING:
# print(f"[DEBUG] register consents={consents}")

OFFLINE DOCKER TEST CASES:
- A failed registration (duplicate email) leaves zero consent rows.
"""
SEEDEOF
seed_note "app/auth/routes.py (P11 addendum)"

cat << 'SEEDEOF' >> app/public/routes.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (one-click unsubscribe, FLAG 11):
1. GET/POST /unsubscribe/<token>: token = itsdangerous-signed
   (user_id, channel) with a purpose salt (no login needed, tenant host
   required). GET shows a confirm page; POST revokes MARKETING consent for that
   channel via auth_service.record_consent(source='unsubscribe_link'). Invalid/
   tampered token -> 404; token for another tenant's user -> 404. The POST also
   serves the RFC 8058 List-Unsubscribe-Post one-click request.
2. Transactional consent is never touched by this route.

DEBUGGING:
# print(f"[DEBUG] unsubscribe user={user_id} channel={channel}")

OFFLINE DOCKER TEST CASES:
- A valid token revokes marketing consent only; transactional stays active.
- A tampered token returns 404 and changes nothing.
"""
SEEDEOF
seed_note "app/public/routes.py (P11 addendum)"

cat << 'SEEDEOF' > app/api/whatsapp_webhook_resource.py
"""
FILE PURPOSE: HTTP adapter for Meta WhatsApp webhooks — verification
handshake, delivery receipts and STOP opt-outs (FLAGS 3, 13, 14). No tenant
host: org is derived from the matched recipient row.

PROMPT FOR LLM IMPLEMENTATION:
1. `GET`: Meta verification — if hub.mode == 'subscribe' and hub.verify_token
   equals WHATSAPP_WEBHOOK_VERIFY_TOKEN echo hub.challenge, else 403.
2. `POST`: verify X-Hub-Signature-256 against WHATSAPP_APP_SECRET BEFORE any DB
   access (400 on failure). For status events (sent/delivered/read/failed) look
   up message_campaign_recipients by provider_message_id inside tenant_bypass,
   then update delivery_status inside tenant_context(org_id); unknown id ->
   WARNING + 200; monotonic (never move 'delivered' back to 'sent').
3. Inbound text messages equal to STOP / UNSUBSCRIBE (case-insensitive) revoke
   MARKETING consent for every user matching that E.164 number across all
   tenants (auth_service.record_consent source='whatsapp_stop').
4. Always 200 for recognized, signed payloads.

DEBUGGING:
# print(f"[DEBUG] whatsapp webhook type={event_type} msg_id={provider_message_id}")

OFFLINE DOCKER TEST CASES:
- A tampered signature -> 400 and zero DB writes.
- The delivered fixture moves a recipient 'sent' -> 'delivered'; replaying a
  stale 'sent' event does not regress it.
- The STOP fixture revokes marketing consent for the matching user(s) only.
"""
SEEDEOF
seed_note "app/api/whatsapp_webhook_resource.py"

cat << 'SEEDEOF' >> app/api/__init__.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Register WhatsAppWebhookResource at "/whatsapp/webhook" (GET + POST).

DEBUGGING:
# print("[DEBUG] whatsapp webhook registered")

OFFLINE DOCKER TEST CASES:
- GET and POST /api/whatsapp/webhook exist on url_map.
"""
SEEDEOF
seed_note "app/api/__init__.py (P11 addendum)"

cat << 'SEEDEOF' >> app/tenancy/resolver.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add '/api/whatsapp/webhook' to EXEMPT_PATHS (Meta calls it with no tenant host).

DEBUGGING:
# print(f"[DEBUG] exempt paths={EXEMPT_PATHS}")

OFFLINE DOCKER TEST CASES:
- POST /api/whatsapp/webhook on a non-tenant host is not 404'd by the resolver.
"""
SEEDEOF
seed_note "app/tenancy/resolver.py (P11 addendum)"
log "STEP 5 complete: forms, routes, API seeded"

# -----------------------------------------------------------------------------
# STEP 6: Templates
# -----------------------------------------------------------------------------
echo "STEP 6: Seeding templates..."

cat << 'SEEDEOF' > app/templates/admin/campaigns_list.html
<!--
FILE PURPOSE: Campaigns list (UI/UX v2 §3.3). Shared by Admin and permitted Staff.

PROMPT FOR LLM IMPLEMENTATION:
1. Shell chosen by current_user.role (sidebar_admin / sidebar_staff). Table:
   Channel pill, Subject/first line, Status badge (draft/tested/sent),
   Recipients, Created, Actions. "+ New Campaign" button. Empty state.

DEBUGGING:
DEBUG: campaigns_list.html role={{ current_user.role }} count={{ campaigns|length }}

OFFLINE DOCKER TEST CASES:
- A staff viewer sees only their own campaigns.
-->
SEEDEOF
seed_note "app/templates/admin/campaigns_list.html"

cat << 'SEEDEOF' > app/templates/admin/campaign_composer.html
<!--
FILE PURPOSE: Campaign Composer (UI/UX v2 §4.6).

PROMPT FOR LLM IMPLEMENTATION:
1. Sections: Channel; Subject (email only); Message body with a visible list of
   allowed merge fields; WhatsApp template name (whatsapp only); Audience
   builder (the CampaignForm filter fields) with a consent-filtered recipient
   ESTIMATE; Test Send panel (test email + mobile) and a Send panel.
2. Send is rendered ONLY when status == 'tested' (absent, not disabled); a
   notice explains that editing after a test resets to draft. Estimate copy:
   "Only recipients who consented to <channel> marketing are included."
3. All labels visible; flash region ARIA-live.

DEBUGGING:
DEBUG: campaign_composer.html status={{ campaign.status if campaign else 'new' }}

OFFLINE DOCKER TEST CASES:
- A draft campaign's page contains no Send button.
-->
SEEDEOF
seed_note "app/templates/admin/campaign_composer.html"

cat << 'SEEDEOF' > app/templates/admin/campaign_detail.html
<!--
FILE PURPOSE: Campaign detail + delivery stats (extra file, FLAG 13).

PROMPT FOR LLM IMPLEMENTATION:
1. Rendered message (merge fields shown unsubstituted), audience summary,
   status badge, sent_at, recipient_count, counts per delivery_status
   (queued/sent/delivered/read/failed) and a paginated recipient table (first
   name + status only — no email/mobile).

DEBUGGING:
DEBUG: campaign_detail.html campaign={{ campaign.id }}

OFFLINE DOCKER TEST CASES:
- The recipient table never displays email addresses or mobile numbers.
-->
SEEDEOF
seed_note "app/templates/admin/campaign_detail.html"

cat << 'SEEDEOF' > app/templates/trekker/notification_preferences.html
<!--
FILE PURPOSE: Notification Preferences (UI/UX v2 §3.5).

PROMPT FOR LLM IMPLEMENTATION:
1. navbar_trekker shell. A 3×3 grid (channel rows: email/WhatsApp/SMS; columns:
   Enabled, Booking updates (transactional), Offers & news (marketing)), each a
   labeled checkbox; consent timestamps shown as small muted text.
2. Note under the email row: security emails (password reset) are always sent.

DEBUGGING:
DEBUG: notification_preferences.html user={{ current_user.id }}

OFFLINE DOCKER TEST CASES:
- Exactly 9 labeled checkboxes render; the form posts with a CSRF token.
-->
SEEDEOF
seed_note "app/templates/trekker/notification_preferences.html"

cat << 'SEEDEOF' > app/templates/trekker/unsubscribe.html
<!--
FILE PURPOSE: One-click unsubscribe confirm page (extra file, FLAG 13).

PROMPT FOR LLM IMPLEMENTATION:
1. No-login minimal page: "Unsubscribe from <channel> offers from <org name>?"
   with a Confirm button (POST) and a link to full preferences (login required).
   After POST show a confirmation state.

DEBUGGING:
DEBUG: unsubscribe.html channel={{ channel }}

OFFLINE DOCKER TEST CASES:
- The page works for an anonymous visitor and posts to the same token URL.
-->
SEEDEOF
seed_note "app/templates/trekker/unsubscribe.html"

cat << 'SEEDEOF' > app/templates/email/campaign_wrapper.html
<!--
FILE PURPOSE: Branded wrapper for marketing emails (extra file, FLAG 13).

PROMPT FOR LLM IMPLEMENTATION:
1. Tenant branding header (logo/name/colors), the already-merged body as
   escaped plain text (never |safe), footer with organization contact details
   and the signed unsubscribe link.

DEBUGGING:
DEBUG: campaign_wrapper.html org={{ current_org.name }}

OFFLINE DOCKER TEST CASES:
- Body text containing <script> is escaped; the unsubscribe link appears exactly once.
-->
SEEDEOF
seed_note "app/templates/email/campaign_wrapper.html"

cat << 'SEEDEOF' >> app/templates/shared/sidebar_admin.html

<!--
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add a "Campaigns" link.

DEBUGGING:
DEBUG: sidebar_admin.html campaigns link rendered

OFFLINE DOCKER TEST CASES:
- Link is active-styled on admin.campaigns_list.
-->
SEEDEOF
seed_note "app/templates/shared/sidebar_admin.html (P11 addendum)"

cat << 'SEEDEOF' >> app/templates/shared/sidebar_staff.html

<!--
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. "Campaigns" link only when has_capability('campaigns.compose').

DEBUGGING:
DEBUG: sidebar_staff.html campaigns={{ has_capability('campaigns.compose') }}

OFFLINE DOCKER TEST CASES:
- Staff without the capability see no Campaigns link.
-->
SEEDEOF
seed_note "app/templates/shared/sidebar_staff.html (P11 addendum)"

cat << 'SEEDEOF' >> app/templates/shared/navbar_trekker.html

<!--
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add "Notification Preferences" to the authenticated avatar dropdown.

DEBUGGING:
DEBUG: navbar_trekker.html prefs link rendered

OFFLINE DOCKER TEST CASES:
- The link is absent for anonymous visitors.
-->
SEEDEOF
seed_note "app/templates/shared/navbar_trekker.html (P11 addendum)"

cat << 'SEEDEOF' >> app/templates/trekker/booking_detail.html

<!--
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. "Your point of contact" card (name + contact details) when `contact` is
   non-empty; nothing rendered otherwise (no empty card).

DEBUGGING:
DEBUG: booking_detail.html contact={{ contact is not none and contact|length > 0 }}

OFFLINE DOCKER TEST CASES:
- A pending booking shows no point-of-contact card.
-->
SEEDEOF
seed_note "app/templates/trekker/booking_detail.html (P11 addendum)"

cat << 'SEEDEOF' >> app/templates/auth/register.html

<!--
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add an optional "Communication preferences" fieldset: WhatsApp/SMS booking
   updates (transactional) and Offers & news per channel (marketing) —
   all UNCHECKED by default, each with a visible label; text links to /privacy.

DEBUGGING:
DEBUG: register.html consent fieldset rendered

OFFLINE DOCKER TEST CASES:
- No consent checkbox is pre-checked in the rendered HTML.
-->
SEEDEOF
seed_note "app/templates/auth/register.html (P11 addendum)"

cat << 'SEEDEOF' >> app/templates/shared/_badge_macros.html

<!--
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (UI/UX v2 §6):
1. Add domains: 'campaign_status' (draft #6B7280/#EEEFF1, tested
   #0891B2/#E5F5F8, sent #16A34A/#E7F6EC) and 'delivery_status' (queued
   #6B7280/#EEEFF1, sent #0891B2/#E5F5F8, delivered/read #16A34A/#E7F6EC,
   failed #DC2626/#FBE9E9).

DEBUGGING:
DEBUG: status_badge domain={{ domain }} value={{ value }}

OFFLINE DOCKER TEST CASES:
- status_badge('tested','campaign_status') renders #0891B2.
-->
SEEDEOF
seed_note "app/templates/shared/_badge_macros.html (P11 addendum)"
log "STEP 6 complete: templates seeded"

# -----------------------------------------------------------------------------
# STEP 7: Fixtures
# -----------------------------------------------------------------------------
echo "STEP 7: Seeding fixtures..."

cat << 'SEEDEOF' > tests/fixtures/whatsapp_webhook_delivered.json
{
  "_seed_prompt": "FILE PURPOSE: Sample Meta WhatsApp webhook body for a 'delivered' status (TDR v2 §10 fixtures, zero live network). PROMPT FOR LLM IMPLEMENTATION: Replace with a realistic payload: object 'whatsapp_business_account', entry[].changes[].value.statuses[] with 'id' (wamid... matching a provider_message_id used in tests), 'status': 'delivered', 'timestamp', 'recipient_id'. Plain valid JSON, no _seed_prompt key. DEBUGGING: log the status id before use. OFFLINE DOCKER TEST CASES: POSTing it with a correct test X-Hub-Signature-256 moves the matching recipient to 'delivered'."
}
SEEDEOF
seed_note "tests/fixtures/whatsapp_webhook_delivered.json"

cat << 'SEEDEOF' > tests/fixtures/whatsapp_webhook_failed.json
{
  "_seed_prompt": "FILE PURPOSE: Sample Meta WhatsApp webhook body for a 'failed' status. PROMPT FOR LLM IMPLEMENTATION: Same envelope as the delivered fixture with statuses[].status 'failed' and an 'errors' array (code, title). Plain valid JSON, no _seed_prompt key. DEBUGGING: log the status id before use. OFFLINE DOCKER TEST CASES: POSTing it with a valid test signature sets the matching recipient to 'failed'."
}
SEEDEOF
seed_note "tests/fixtures/whatsapp_webhook_failed.json"

cat << 'SEEDEOF' > tests/fixtures/whatsapp_inbound_stop.json
{
  "_seed_prompt": "FILE PURPOSE: Sample inbound WhatsApp message 'STOP' (opt-out handling, FLAG 3). PROMPT FOR LLM IMPLEMENTATION: Realistic payload: entry[].changes[].value.messages[] with 'from' (an E.164 number matching a test user), 'type': 'text', 'text.body': 'STOP'. Plain valid JSON, no _seed_prompt key. DEBUGGING: log the 'from' number before use. OFFLINE DOCKER TEST CASES: POSTing it with a valid test signature revokes marketing consent for the matching user(s) and leaves transactional consent untouched."
}
SEEDEOF
seed_note "tests/fixtures/whatsapp_inbound_stop.json"
log "STEP 7 complete: fixtures seeded"

# -----------------------------------------------------------------------------
# STEP 8: App factory addendum
# -----------------------------------------------------------------------------
echo "STEP 8: Appending P11 addendum to app/__init__.py..."
cat << 'SEEDEOF' >> app/__init__.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (extend create_app()):
1. Validate comms config at startup: EMAIL_PROVIDER in (smtp, sendgrid, ses),
   SMS_PROVIDER, WHATSAPP_PROVIDER recognized; for non-smtp providers in a
   non-debug app, missing credentials raise a clear error (FLAG 1).
2. CLI `flask backfill-consents`: auth_service.backfill_consents() for every
   org (inside tenant_bypass); prints counts; idempotent.
3. No new blueprint — the WhatsApp webhook registers via app.api, the composer
   via admin_bp, the unsubscribe route via public_bp.

DEBUGGING:
# print(f"[DEBUG] comms providers email={app.config.get('EMAIL_PROVIDER')} sms={app.config.get('SMS_PROVIDER')} wa={app.config.get('WHATSAPP_PROVIDER')}")

OFFLINE DOCKER TEST CASES:
- create_app() with EMAIL_PROVIDER=carrier-pigeon raises a clear error.
- `flask backfill-consents` twice leaves one email/transactional row per user.
"""
SEEDEOF
seed_note "app/__init__.py (P11 addendum)"
log "STEP 8 complete: app factory addendum appended"

# -----------------------------------------------------------------------------
# STEP 9: Tests
# -----------------------------------------------------------------------------
echo "STEP 9: Seeding tests..."

cat << 'SEEDEOF' > tests/unit/test_notification_dispatch.py
"""
FILE PURPOSE: Unit tests for the single send path in notification_service.

PROMPT FOR LLM IMPLEMENTATION:
1. Cover every OFFLINE TEST CASE in the P11 addendum of notification_service
   with provider seams monkeypatched (zero live calls): consent gate,
   preference gate, critical carve-out, UNSET mobile skip, unmapped WhatsApp
   template, marketing wrapper + unsubscribe header.

DEBUGGING:
# print(f"[DEBUG] dispatch unit status={status}")

OFFLINE DOCKER TEST CASES:
- Provider mocks record zero calls for every 'skipped' outcome.
"""
SEEDEOF
seed_note "tests/unit/test_notification_dispatch.py"

cat << 'SEEDEOF' > tests/unit/test_campaign_service.py
"""
FILE PURPOSE: Unit tests for services/campaign_service.py.

PROMPT FOR LLM IMPLEMENTATION:
1. Cover every OFFLINE TEST CASE in the service docstring, including the
   template-injection payloads and the reset-to-draft rule.

DEBUGGING:
# print(f"[DEBUG] campaign unit status={campaign.status}")

OFFLINE DOCKER TEST CASES:
- Merge-field rendering never evaluates author-supplied template syntax.
"""
SEEDEOF
seed_note "tests/unit/test_campaign_service.py"

cat << 'SEEDEOF' > tests/unit/test_audience_service.py
"""
FILE PURPOSE: Unit tests for services/audience_service.py.

PROMPT FOR LLM IMPLEMENTATION:
1. Cover every OFFLINE TEST CASE in the service docstring plus each whitelisted
   filter key individually and in combination.

DEBUGGING:
# print(f"[DEBUG] audience unit resolved={len(users)}")

OFFLINE DOCKER TEST CASES:
- The consent intersection cannot be disabled by any filter combination.
"""
SEEDEOF
seed_note "tests/unit/test_audience_service.py"

cat << 'SEEDEOF' > tests/integration/test_consent_filtering.py
"""
FILE PURPOSE: Consent-filtering tests required by TDR v2 §10.

PROMPT FOR LLM IMPLEMENTATION:
1. A marketing campaign whose audience filter matches a recipient holding ONLY
   transactional consent never reaches that recipient (provider mock never
   called for them), even when built by the Tenant Admin; consent revoked
   between draft and send is honored; per-channel isolation (email consent does
   not authorize WhatsApp).

DEBUGGING:
# print(f"[DEBUG] consent filter matched={matched} sent={sent}")

OFFLINE DOCKER TEST CASES:
- Zero marketing messages are delivered to transactional-only consenters.
"""
SEEDEOF
seed_note "tests/integration/test_consent_filtering.py"

cat << 'SEEDEOF' > tests/integration/test_campaign_flow.py
"""
FILE PURPOSE: Integration tests for the composer flow end to end.

PROMPT FOR LLM IMPLEMENTATION:
1. Compose -> estimate -> test-send (status 'tested', no recipient rows) ->
   send (recipients created, status 'sent'); send before test refused; edit
   after test resets to draft; staff capability + own-campaigns-only scoping;
   rate limits; cross-tenant isolation.

DEBUGGING:
# print(f"[DEBUG] campaign flow id={campaign.id} status={campaign.status}")

OFFLINE DOCKER TEST CASES:
- The full flow passes with all providers mocked.
"""
SEEDEOF
seed_note "tests/integration/test_campaign_flow.py"

cat << 'SEEDEOF' > tests/integration/test_notification_preferences.py
"""
FILE PURPOSE: Integration tests for the preference center and registration opt-ins.

PROMPT FOR LLM IMPLEMENTATION:
1. Registration opt-in combinations; preference toggles write consents/audit
   rows; cross-user access blocked; backfill CLI behavior.

DEBUGGING:
# print(f"[DEBUG] preferences test user={user.id}")

OFFLINE DOCKER TEST CASES:
- No consent is ever granted without an explicit user action (except email transactional).
"""
SEEDEOF
seed_note "tests/integration/test_notification_preferences.py"

cat << 'SEEDEOF' > tests/integration/test_whatsapp_webhook.py
"""
FILE PURPOSE: Integration tests for the WhatsApp webhook.

PROMPT FOR LLM IMPLEMENTATION:
1. Verify handshake (good/bad token); signature rejection; delivered/failed
   fixtures update recipients monotonically; STOP fixture revokes marketing
   consent only; unknown ids tolerated; no tenant host needed.

DEBUGGING:
# print(f"[DEBUG] whatsapp webhook test status={resp.status_code}")

OFFLINE DOCKER TEST CASES:
- Bad signature -> 400 with zero DB writes.
"""
SEEDEOF
seed_note "tests/integration/test_whatsapp_webhook.py"

cat << 'SEEDEOF' > tests/integration/test_unsubscribe.py
"""
FILE PURPOSE: Integration tests for the unsubscribe link/endpoint.

PROMPT FOR LLM IMPLEMENTATION:
1. Valid token revokes marketing for that channel only; tampered/cross-tenant
   tokens -> 404; List-Unsubscribe headers present on marketing emails and
   absent on transactional ones.

DEBUGGING:
# print(f"[DEBUG] unsubscribe test status={resp.status_code}")

OFFLINE DOCKER TEST CASES:
- Transactional consent survives every unsubscribe call.
"""
SEEDEOF
seed_note "tests/integration/test_unsubscribe.py"

cat << 'SEEDEOF' >> tests/isolation/test_tenant_scoping.py

"""
P11 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add model factories for notification_consents, notification_preferences,
   message_campaigns, message_campaign_recipients; add HTTP isolation cases for
   campaigns, preferences and the unsubscribe token across tenants.

DEBUGGING:
# print(f"[DEBUG] isolation P11 model={model.__name__}")

OFFLINE DOCKER TEST CASES:
- Every new tenant-scoped model passes read/get/update/delete isolation.
"""
SEEDEOF
seed_note "tests/isolation/test_tenant_scoping.py (P11 addendum)"
log "STEP 9 complete: tests seeded"

# -----------------------------------------------------------------------------
# STEP 10: Commits (left for after code-implementer fills in real logic)
# -----------------------------------------------------------------------------
#git add app/services/notification_service.py app/api/whatsapp_webhook_resource.py requirements.txt
#git commit -m "feat(comms): production whatsapp/email/sms integration"
#git add app/models app/services/auth_service.py app/trekker
#git commit -m "feat(comms): consent and notification-preference model"
#git add app/admin app/services/campaign_service.py app/services/audience_service.py app/templates
#git commit -m "feat(comms): campaign composer with segment targeting and test-send"
#git push -u origin "$BRANCH_NAME"

# -----------------------------------------------------------------------------
# STEP 11: Summary
# -----------------------------------------------------------------------------
echo ""
echo "====================================================="
echo "Milestone P11 (v2) Seeded Successfully!"
echo "-----------------------------------------------------"
echo "Seed files created this run:"
for f in "${SEED_FILES[@]}"; do echo "  - $f"; done
echo "-----------------------------------------------------"
echo "NEEDS YOUR DECISION (see P11.sh header): WhatsApp sender per tenant,"
echo "India SMS DLT, on-ground team table, SMS-OTP mobile verification."
echo "-----------------------------------------------------"
echo "NEXT STEPS:"
echo "1. Copy new keys from .env.example into .env; docker compose build."
echo "2. Feed these seeds to the code-implementer skill."
echo "3. flask seed-rbac && flask backfill-consents"
echo "4. docker compose run --rm test   (providers are mocked — no live calls)"
echo "5. Open a PR: $BRANCH_NAME -> main once tests pass."
echo "6. Execute P12.sh (Support v2)."
echo "====================================================="
log "===== P11.sh (v2) completed successfully ====="

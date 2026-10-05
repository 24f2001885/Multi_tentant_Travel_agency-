#!/usr/bin/env bash
###############################################################################
# P10.sh — v2 MILESTONE 10: Trek Content & Discovery
# TMA Platform — Multi-Tenant Trekking Management SaaS
#
# Covers (Master Project Blueprint v2 §3, phase P10):
#   - Structured day/element itinerary + branded PDF export
#   - Multi-image media gallery
#   - Post-completion trek reviews
#   - Staff-created treks (land pending + unassigned, Admin assigns/approves)
#
# Depends on: P7 (tenancy, public routes), P8 (capabilities). P9 is NOT a hard
# dependency (Blueprint: parallelizable) — branch base falls back to P8.
# Never writes real application logic — only git/dir/config setup + PROMPT seeds.
#
# FLAGGED DECISIONS:
#  1. treks.created_by_user_id (Integer FK users.id, nullable) is added — NOT in
#     TDR v2 — because "the Staff member who created the trek may edit its
#     itinerary" (PDR v2 §3) needs a creator reference.
#  2. New child tables carry organization_id via TenantScopedMixin (TDR §1 hard
#     gate) although TDR §4.4 omits it on itinerary/media/review tables.
#  3. MEDIA STORAGE is unspecified in the TDR. Seeded as local disk under
#     MEDIA_STORAGE_PATH (default instance/uploads) behind media_service, so it
#     can later swap to object storage. Multi-instance caveat applies (same as
#     invoices). trek_media.asset_url stores an INTERNAL storage key, not a URL;
#     images are served by GET /media/<uuid> (tenant-scoped), never a guessable
#     static path. Uploads are decoded with Pillow (JPEG/PNG/WEBP only, no SVG),
#     re-encoded to strip EXIF, renamed to UUIDs, size-capped.
#  4. CONTENT EDIT RULE: Tenant Admin, or Staff holding 'treks.edit_content' who
#     CREATED or is ASSIGNED to the trek (new decorator
#     trek_content_editor_required). Core trek fields stay Admin-only after
#     creation (v1 rule); Staff set them only at creation.
#  5. Reviews: one per completed booking; NO edit/delete/moderation (the docs
#     specify none) — say if you want moderation. Review-request email fires
#     from the existing trek-completion cascade.
#  6. Public trek pages show open/closed/completed treks only (pending/approved
#     remain invisible), so completed treks keep their reviews visible.
#  7. Itinerary day_number must be <= trek.duration_days (integrity guard).
#  8. Extra files beyond the Blueprint tree: shared/_itinerary_builder.html,
#     shared/_media_manager.html, trekker/itinerary_pdf.html,
#     trekker/review_form.html, staff/trek_create.html, email/review_request.html.
#  9. New dependency: Pillow. WeasyPrint (added in P9) is required for the PDF —
#     if you skipped P9, this script adds it but your Dockerfile also needs
#     libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 (a warning prints).
# 10. New env vars: MEDIA_STORAGE_PATH, MEDIA_MAX_UPLOAD_MB.
#
# USAGE:
#   cd ~/Desktop/t
#   bash ./docs/P10.sh
###############################################################################

set -euo pipefail

BRANCH_NAME="phase/P10-trek-content-discovery"
LOG_FILE="./docs/p10_v2_milestone.log"

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
log "===== P10.sh (v2) started — Trek Content & Discovery ====="

# -----------------------------------------------------------------------------
# STEP 0: Pre-flight
# -----------------------------------------------------------------------------
echo "STEP 0: Pre-flight checks..."
[ -d ".git" ] || { echo "ERROR: no .git found."; exit 1; }
for f in app/tenancy/scoping.py app/services/rbac_service.py app/public/routes.py \
         app/services/trek_service.py app/services/notification_service.py \
         app/auth/decorators.py; do
  [ -f "$f" ] || { echo "ERROR: $f missing — run P7.sh/P8.sh first."; exit 1; }
done
log "STEP 0 complete: pre-flight checks passed"

# -----------------------------------------------------------------------------
# STEP 1: Branch (base: P9 if it exists, else P8)
# -----------------------------------------------------------------------------
echo "STEP 1: Checking out milestone branch..."
BASE_BRANCH=""
for b in phase/P9-booking-money-engine phase/P8-identity-access-governance; do
  if git show-ref --verify --quiet "refs/heads/$b"; then BASE_BRANCH="$b"; break; fi
done
[ -n "$BASE_BRANCH" ] || { echo "ERROR: neither P9 nor P8 branch found."; exit 1; }
checkout_phase_branch "$BASE_BRANCH" "$BRANCH_NAME"
log "STEP 1 complete: on $BRANCH_NAME (base: $BASE_BRANCH)"

# -----------------------------------------------------------------------------
# STEP 2: Directories + infra/config
# -----------------------------------------------------------------------------
echo "STEP 2: Creating directories and config..."
mkdir -p tests/unit tests/integration instance/uploads

ensure_req "Pillow>=10.0,<12.0"
ensure_req "weasyprint>=60,<70"
seed_note "requirements.txt (P10: Pillow, weasyprint)"
if ! grep -qi "libpango" Dockerfile 2>/dev/null; then
  echo "  !! WARNING: Dockerfile lacks WeasyPrint system libs (libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0). Run P9.sh or add them."
  log "WARNING: Dockerfile missing WeasyPrint system libs"
fi

if ! grep -q "TMA Platform v2 (P10)" .env.example; then
cat << 'SEEDEOF' >> .env.example

# --- TMA Platform v2 (P10) ---
MEDIA_STORAGE_PATH=instance/uploads
MEDIA_MAX_UPLOAD_MB=5
SEEDEOF
fi
seed_note ".env.example"
echo "  !! Copy the new keys from .env.example into your real .env."
log "STEP 2 complete: directories, requirements, .env.example"

# -----------------------------------------------------------------------------
# STEP 3: Models
# -----------------------------------------------------------------------------
echo "STEP 3: Seeding models..."

cat << 'SEEDEOF' > app/models/trek_itinerary_day.py
"""
FILE PURPOSE: The `trek_itinerary_days` table — one row per day of a trek's
structured itinerary (TDR v2 §4.4).

PROMPT FOR LLM IMPLEMENTATION:
1. Inherit TenantScopedMixin (header FLAG 2). Columns: id (UUID PK), trek_id
   (Integer FK treks.id NOT NULL, ON DELETE CASCADE), day_number (Integer NOT
   NULL, CHECK >= 1), title (String(150) NOT NULL), description (Text null).
   UniqueConstraint(trek_id, day_number).
2. Relationship `elements` (ordered by sequence, cascade delete-orphan) and
   back-reference to Trek.itinerary_days (ordered by day_number).

DEBUGGING:
# print(f"[DEBUG] itinerary_day trek={self.trek_id} day={self.day_number}")

OFFLINE DOCKER TEST CASES:
- Two days with the same (trek_id, day_number) raise IntegrityError.
- Deleting a day deletes its elements.
"""
SEEDEOF
seed_note "app/models/trek_itinerary_day.py"

cat << 'SEEDEOF' > app/models/itinerary_element.py
"""
FILE PURPOSE: The `itinerary_elements` table — ordered items within an
itinerary day (TDR v2 §4.4).

PROMPT FOR LLM IMPLEMENTATION:
1. Inherit TenantScopedMixin. Columns: id (UUID PK), itinerary_day_id (UUID FK
   trek_itinerary_days.id NOT NULL, ON DELETE CASCADE), sequence (Integer NOT
   NULL, CHECK >= 1), element_type (String(20) NOT NULL, CHECK IN
   ('activity','meal','transport','accommodation','note')), title (String(150)
   NOT NULL), description (Text null), time_of_day (String(20) null).
   UniqueConstraint(itinerary_day_id, sequence).

DEBUGGING:
# print(f"[DEBUG] itinerary_element day={self.itinerary_day_id} seq={self.sequence} type={self.element_type}")

OFFLINE DOCKER TEST CASES:
- element_type='ski' raises IntegrityError.
- Duplicate (day, sequence) raises IntegrityError.
"""
SEEDEOF
seed_note "app/models/itinerary_element.py"

cat << 'SEEDEOF' > app/models/trek_media.py
"""
FILE PURPOSE: The `trek_media` table — a trek's image gallery (TDR v2 §4.4).

PROMPT FOR LLM IMPLEMENTATION:
1. Inherit TenantScopedMixin. Columns: id (UUID PK), trek_id (Integer FK
   treks.id NOT NULL, ON DELETE CASCADE), asset_url (String(500) NOT NULL —
   an INTERNAL storage key, see header FLAG 3), caption (String(255) null),
   sort_order (Integer NOT NULL default 0), uploaded_by (Integer FK users.id),
   created_at (DateTime(timezone=True) default now).
2. Index (trek_id, sort_order). Relationship via Trek.media (ordered).

DEBUGGING:
# print(f"[DEBUG] trek_media trek={self.trek_id} order={self.sort_order}")

OFFLINE DOCKER TEST CASES:
- Deleting a trek deletes its media rows (files are removed by media_service).
"""
SEEDEOF
seed_note "app/models/trek_media.py"

cat << 'SEEDEOF' > app/models/trek_review.py
"""
FILE PURPOSE: The `trek_reviews` table — one review per completed booking
(TDR v2 §4.4).

PROMPT FOR LLM IMPLEMENTATION:
1. Inherit TenantScopedMixin. Columns: id (UUID PK), trek_id (Integer FK),
   booking_id (Integer FK bookings.id, UNIQUE), user_id (Integer FK users.id),
   rating (Integer NOT NULL, CHECK BETWEEN 1 AND 5), comment (Text null),
   created_at (DateTime(timezone=True) default now). Index (trek_id, created_at).

DEBUGGING:
# print(f"[DEBUG] review trek={self.trek_id} booking={self.booking_id} rating={self.rating}")

OFFLINE DOCKER TEST CASES:
- rating=6 raises IntegrityError; a second review for one booking raises IntegrityError.
"""
SEEDEOF
seed_note "app/models/trek_review.py"

cat << 'SEEDEOF' >> app/models/trek.py

"""
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (modify this model):
1. Add created_by_user_id (Integer FK users.id, NULLABLE) — header FLAG 1.
   Admin-created treks store the admin; staff-created store the staff user.
2. Relationships: itinerary_days (ordered by day_number), media (ordered by
   sort_order), reviews; all cascade delete.

DEBUGGING:
# print(f"[DEBUG] trek {self.id} created_by={self.created_by_user_id}")

OFFLINE DOCKER TEST CASES:
- Existing treks keep created_by_user_id NULL; deleting a trek cascades to
  itinerary, media and review rows.
"""
SEEDEOF
seed_note "app/models/trek.py (P10 addendum)"

cat << 'SEEDEOF' >> app/models/__init__.py

"""
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Import and add to __all__: TrekItineraryDay, ItineraryElement, TrekMedia,
   TrekReview.

DEBUGGING:
# print(f"[DEBUG] models package exports {len(__all__)}")

OFFLINE DOCKER TEST CASES:
- db.metadata.tables now also contains trek_itinerary_days,
  itinerary_elements, trek_media, trek_reviews.
"""
SEEDEOF
seed_note "app/models/__init__.py (P10 addendum)"
log "STEP 3 complete: models seeded"

# -----------------------------------------------------------------------------
# STEP 4: Services
# -----------------------------------------------------------------------------
echo "STEP 4: Seeding services..."

cat << 'SEEDEOF' > app/services/itinerary_service.py
"""
FILE PURPOSE: Structured itinerary editing + PDF export (TDR v2 §4.4; PDR v2
§5.6). Framework-agnostic; callers enforce edit rights.

PROMPT FOR LLM IMPLEMENTATION:
1. Day ops: `add_day(trek, title, description=None)` (next day_number;
   must be <= trek.duration_days else ItineraryLimitError), `update_day`,
   `delete_day` (re-numbers later days contiguously — avoid transient
   UNIQUE(trek_id, day_number) violations, e.g. two-phase update).
2. Element ops: `add_element(day, element_type, title, description=None,
   time_of_day=None)` appends the next sequence; `update_element`;
   `delete_element` (re-sequence); `move_element(element, direction)` up/down
   swap (no JS drag-and-drop). element_type whitelisted to the five TDR types,
   else InvalidElementTypeError.
3. `get_itinerary(trek)` -> ordered nested structure for templates/PDF.
4. `render_pdf(trek) -> bytes`: WeasyPrint from trekker/itinerary_pdf.html with
   the org's branding (name, logo, colors, contact) — offline, no remote assets.
5. Every mutation writes AuditLog 'itinerary.update' (no commit; caller commits).

DEBUGGING:
# print(f"[DEBUG] itinerary trek={trek.id} days={len(days)} action={action}")

OFFLINE DOCKER TEST CASES:
- Deleting day 2 of 3 leaves days numbered 1,2.
- add_day beyond duration_days raises ItineraryLimitError.
- move_element on the first element 'up' is a no-op, not an error.
- render_pdf returns bytes starting with %PDF for a trek with 2 days.
"""
SEEDEOF
seed_note "app/services/itinerary_service.py"

cat << 'SEEDEOF' > app/services/media_service.py
"""
FILE PURPOSE: Trek gallery storage behind a swappable interface (TDR v2 §4.4).
Framework-agnostic apart from the uploaded-file object; see header FLAG 3.

PROMPT FOR LLM IMPLEMENTATION:
1. `save_upload(trek, file_obj, uploader, caption=None) -> TrekMedia`: enforce
   MEDIA_MAX_UPLOAD_MB; decode with Pillow to verify it is a real JPEG/PNG/WEBP
   (reject SVG/other with InvalidMediaError); re-encode to strip EXIF; store as
   <MEDIA_STORAGE_PATH>/<org_id>/<trek_id>/<uuid>.<ext> (original filename is
   NEVER used); asset_url = that relative key; sort_order = max+1.
2. `update_caption(media, caption)`, `move(media, direction)` (swap
   sort_order with neighbour), `delete_media(media)` (row + file),
   `delete_all_for_trek(trek)` (called by the Admin trek-delete route).
3. `get_cover(trek)` = lowest sort_order item or None.
4. `open_for_serving(media) -> (path, mimetype)` for the tenant-scoped
   GET /media/<uuid> route (path-traversal safe: resolve and assert it stays
   under MEDIA_STORAGE_PATH).

DEBUGGING:
# print(f"[DEBUG] media trek={trek.id} size={size} fmt={fmt}")

OFFLINE DOCKER TEST CASES:
- A renamed .exe or SVG uploaded as .jpg is rejected and leaves no file/row.
- Saved files carry no EXIF and a UUID name.
- delete_media removes both the row and the file; a traversal key never
  resolves outside MEDIA_STORAGE_PATH.
"""
SEEDEOF
seed_note "app/services/media_service.py"

cat << 'SEEDEOF' > app/services/review_service.py
"""
FILE PURPOSE: Post-completion trek reviews (PDR v2 §5.10).
Framework-agnostic.

PROMPT FOR LLM IMPLEMENTATION:
1. `create_review(user, booking, rating, comment=None)`: booking.user_id must
   equal user.id (else ReviewNotAllowedError); booking.status must be
   'completed'; rating int 1..5; comment <= 2000 chars; one review per booking
   (DuplicateReviewError). No commit.
2. `list_reviews(trek, page, per_page)` newest first (reviewer first name only —
   never email/mobile); `average_rating(trek) -> (avg, count)` via an aggregate
   query (no cached value that can drift, PDR §5.6 spirit).
3. `can_review(user, booking) -> bool` for template gating.

DEBUGGING:
# print(f"[DEBUG] review trek={booking.trek_id} user={user.id} rating={rating}")

OFFLINE DOCKER TEST CASES:
- Reviewing a 'booked' (not completed) booking raises ReviewNotAllowedError.
- Reviewing someone else's booking raises ReviewNotAllowedError.
- A second review on one booking raises DuplicateReviewError.
- list_reviews exposes only the reviewer's first name.
"""
SEEDEOF
seed_note "app/services/review_service.py"

cat << 'SEEDEOF' >> app/services/trek_service.py

"""
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. `create_trek_as_staff(creator, data) -> Trek`: status 'pending',
   assigned_staff_id None, created_by_user_id=creator.id,
   available_slots=total_slots, validations identical to the Admin TrekForm
   rules; AuditLog 'trek.create_by_staff'. No commit.
2. `can_edit_content(user, trek) -> bool`: Tenant Admin True; staff True only
   when they hold 'treks.edit_content' AND (trek.created_by_user_id == user.id
   OR trek.assigned_staff_id == their staff_profile.id); else False.
3. Extend complete_trek_bookings() (P5): after flipping bookings to
   'completed', call notification_service.send_review_request_email for each
   affected booking (failures logged, never raised).

DEBUGGING:
# print(f"[DEBUG] can_edit_content user={user.id} trek={trek.id} -> {result}")

OFFLINE DOCKER TEST CASES:
- A staff-created trek is pending, unassigned, with available_slots == total_slots.
- Staff who is neither creator nor assignee gets can_edit_content False.
- Completing a trek with 2 booked bookings sends exactly 2 review requests.
"""
SEEDEOF
seed_note "app/services/trek_service.py (P10 addendum)"

cat << 'SEEDEOF' >> app/services/notification_service.py

"""
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. `send_review_request_email(booking)`: renders email/review_request.html
   with trek name and a link to the "Leave a Review" page; same
   try/except-log-don't-raise contract as the existing senders.

DEBUGGING:
# print(f"[DEBUG] review request sent booking={booking.id}")

OFFLINE DOCKER TEST CASES:
- Exactly one captured message per call; an SMTP failure is logged, not raised.
"""
SEEDEOF
seed_note "app/services/notification_service.py (P10 addendum)"

cat << 'SEEDEOF' >> app/services/rbac_service.py

"""
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (append to CAPABILITY_CATALOG):
1. 'treks.create' — "Create treks (land pending, unassigned)";
   'treks.edit_content' — "Edit itinerary and media of treks you created or
   are assigned to". Neither is protected.

DEBUGGING:
# print(f"[DEBUG] catalog size={len(CAPABILITY_CATALOG)}")

OFFLINE DOCKER TEST CASES:
- `flask seed-rbac` adds both keys without duplicating existing ones.
"""
SEEDEOF
seed_note "app/services/rbac_service.py (P10 addendum)"
log "STEP 4 complete: services seeded"

# -----------------------------------------------------------------------------
# STEP 5: Decorator, forms, routes
# -----------------------------------------------------------------------------
echo "STEP 5: Seeding decorator, forms and routes..."

cat << 'SEEDEOF' >> app/auth/decorators.py

"""
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (append to this module):
1. `trek_content_editor_required(view)`: for routes with a trek id param.
   Loads the Trek (404 if missing), then passes only if
   trek_service.can_edit_content(current_user, trek); else abort(403). Sets
   g.trek. Stack beneath @login_required (and @approved_staff_required for
   staff) — this is NOT @staff_owns_trek: a creator of a still-unassigned trek
   must pass.

DEBUGGING:
# print(f"[DEBUG] content editor check user={current_user.id} trek={trek.id}")

OFFLINE DOCKER TEST CASES:
- Creator of an unassigned pending trek passes; an unrelated staff member gets 403.
- Staff without 'treks.edit_content' get 403 even as assignee.
"""
SEEDEOF
seed_note "app/auth/decorators.py (P10 addendum)"

cat << 'SEEDEOF' >> app/admin/forms.py

"""
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. ItineraryDayForm (title ≤150 required, description optional),
   ItineraryElementForm (element_type SelectField of the five TDR types,
   title ≤150, description, time_of_day ≤20), MediaUploadForm (Flask-WTF
   FileField with FileAllowed jpg/jpeg/png/webp + caption ≤255),
   MediaCaptionForm. Server-side limits stay authoritative (media_service).

DEBUGGING:
# print(f"[DEBUG] content form errors: {form.errors}")

OFFLINE DOCKER TEST CASES:
- ItineraryElementForm rejects element_type='ski'.
"""
SEEDEOF
seed_note "app/admin/forms.py (P10 addendum)"

cat << 'SEEDEOF' >> app/staff/forms.py

"""
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. StaffTrekCreateForm: name, location, difficulty (easy|moderate|hard),
   duration_days, total_slots, price, start_date, end_date — NO status,
   assigned staff or available_slots fields (those are Admin-controlled).
   Same validation rules as admin TrekForm (end >= start, numeric ranges).
2. Reuse the content forms from app.admin.forms for itinerary/media endpoints.

DEBUGGING:
# print(f"[DEBUG] StaffTrekCreateForm errors: {form.errors}")

OFFLINE DOCKER TEST CASES:
- StaffTrekCreateForm has no 'status' field; end_date before start_date fails.
"""
SEEDEOF
seed_note "app/staff/forms.py (P10 addendum)"

cat << 'SEEDEOF' >> app/trekker/forms.py

"""
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. ReviewForm: rating (RadioField 1–5, required), comment (TextAreaField,
   optional, ≤2000).

DEBUGGING:
# print(f"[DEBUG] ReviewForm errors: {form.errors}")

OFFLINE DOCKER TEST CASES:
- ReviewForm.validate() is False without a rating.
"""
SEEDEOF
seed_note "app/trekker/forms.py (P10 addendum)"

cat << 'SEEDEOF' >> app/admin/routes.py

"""
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (itinerary + media, Admin side):
1. Trek create route: after saving, redirect to the EDIT page (content needs a
   trek id); set created_by_user_id=current_user.id.
2. Endpoints under /admin/treks/<hashid:id>/ (all @role_required("admin") +
   trek_content_editor_required, POST, CSRF): itinerary/days/add,
   itinerary/days/<uuid:day_id>/update, .../delete,
   itinerary/days/<uuid:day_id>/elements/add, itinerary/elements/<uuid:eid>/
   update, .../delete, .../move (direction up|down); media/upload,
   media/<uuid:mid>/caption, media/<uuid:mid>/move, media/<uuid:mid>/delete.
   All call itinerary_service/media_service and flash.
3. Trek delete route (P2 item 7): call media_service.delete_all_for_trek before
   deleting the row.
4. treks_list gets a "Created by" column and an "Awaiting assignment" filter
   (status 'pending' AND assigned_staff_id NULL).

DEBUGGING:
# print(f"[DEBUG] admin content route {request.path}")

OFFLINE DOCKER TEST CASES:
- Uploading a non-image file flashes an error and writes nothing.
- Deleting a trek removes its media files from disk.
- The "Awaiting assignment" filter lists a staff-created pending trek.
"""
SEEDEOF
seed_note "app/admin/routes.py (P10 addendum)"

cat << 'SEEDEOF' >> app/staff/routes.py

"""
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (staff-created treks + content):
1. GET/POST /staff/treks/new (endpoint staff.trek_create):
   @requires_capability('treks.create'); StaffTrekCreateForm ->
   trek_service.create_trek_as_staff; flash "awaiting Admin assignment and
   approval"; redirect to the content page for that trek.
2. Mirror the Admin content endpoint set under /staff/treks/<hashid:id>/content/
   ... (itinerary days/elements/media) guarded by
   @requires_capability('treks.edit_content') + @trek_content_editor_required,
   calling the same services. A Staff-created trek is NOT reachable through
   @staff_owns_trek until assigned — content routes are the only ones open to
   the creator.
3. Staff dashboard lists "My created treks (pending)" with their status badge
   for those holding treks.create.

DEBUGGING:
# print(f"[DEBUG] staff content route {request.path} user={current_user.id}")

OFFLINE DOCKER TEST CASES:
- A staff-created trek lands pending/unassigned and the creator can add a day.
- The creator cannot reach /staff/treks/<id>/manage for it (not assigned).
"""
SEEDEOF
seed_note "app/staff/routes.py (P10 addendum)"

cat << 'SEEDEOF' >> app/trekker/routes.py

"""
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (reviews):
1. GET/POST /trekker/bookings/<hashid:id>/review (endpoint
   trekker.review_new): owner only; review_service.can_review gates both
   verbs (403/redirect with flash otherwise); POST -> create_review, redirect
   to booking detail.
2. booking_detail context adds `can_review`.

DEBUGGING:
# print(f"[DEBUG] review route booking={id} can_review={can}")

OFFLINE DOCKER TEST CASES:
- Visiting the review page for a 'booked' booking is refused.
- A completed booking can be reviewed exactly once.
"""
SEEDEOF
seed_note "app/trekker/routes.py (P10 addendum)"

cat << 'SEEDEOF' >> app/public/routes.py

"""
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION (public content):
1. GET /treks/<hashid:id>/itinerary/pdf: visible treks only (same visibility as
   detail, FLAG 6); itinerary_service.render_pdf; Content-Disposition
   attachment; trek without itinerary -> 404.
2. GET /treks/<hashid:id>/reviews: paginated public review list (?page=,
   ?per_page= default 20/max 100) + average.
3. GET /media/<uuid:id>: tenant-scoped lookup (other tenant's id -> 404),
   media_service.open_for_serving, long Cache-Control (ids never change).
4. Trek detail context adds gallery, itinerary, average rating + first reviews.

DEBUGGING:
# print(f"[DEBUG] public content route {request.path}")

OFFLINE DOCKER TEST CASES:
- Anonymous GET on a visible trek's PDF returns application/pdf.
- GET /media/<other tenant's uuid> returns 404.
- A pending trek's PDF and reviews return 404.
"""
SEEDEOF
seed_note "app/public/routes.py (P10 addendum)"
log "STEP 5 complete: decorator, forms, routes seeded"

# -----------------------------------------------------------------------------
# STEP 6: Templates
# -----------------------------------------------------------------------------
echo "STEP 6: Seeding templates..."

cat << 'SEEDEOF' > app/templates/shared/_itinerary_builder.html
<!--
FILE PURPOSE: Reusable itinerary editor partial embedded in the Admin trek
edit form and the Staff content page (UI/UX v2 §3.6; extra file, FLAG 8).

PROMPT FOR LLM IMPLEMENTATION:
1. Context: `trek`, `itinerary`, and a `base_url` for the endpoint set (admin
   or staff). Render each day as a card: title/description inline edit form,
   delete, and its ordered elements (type pill, title, time, up/down/delete
   buttons — plain POST forms, no JS dependency).
2. "Add element" form per day (type select of the five types) and an "Add day"
   form (disabled with a note when day count == duration_days).
3. Every form carries a CSRF token; all labels visible (UI/UX v2 §8).

DEBUGGING:
DEBUG: _itinerary_builder.html days={{ itinerary|length }}

OFFLINE DOCKER TEST CASES:
- With days == duration_days the "Add day" control is disabled with its reason.
-->
SEEDEOF
seed_note "app/templates/shared/_itinerary_builder.html"

cat << 'SEEDEOF' > app/templates/shared/_media_manager.html
<!--
FILE PURPOSE: Reusable gallery manager partial (UI/UX v2 §3.6; extra file).

PROMPT FOR LLM IMPLEMENTATION:
1. Upload form (multipart, file + caption, CSRF), grid of thumbnails served via
   /media/<uuid>, each with caption edit, move left/right, delete (confirm).
2. Helper text: JPEG/PNG/WEBP only, max MEDIA_MAX_UPLOAD_MB MB. Every image has
   alt text from its caption (fallback: trek name).

DEBUGGING:
DEBUG: _media_manager.html items={{ trek.media|length }}

OFFLINE DOCKER TEST CASES:
- Each thumbnail <img> has a non-empty alt attribute.
-->
SEEDEOF
seed_note "app/templates/shared/_media_manager.html"

cat << 'SEEDEOF' > app/templates/trekker/itinerary_pdf.html
<!--
FILE PURPOSE: WeasyPrint document template for the branded itinerary PDF
(PDR v2 §5.6; extra file, FLAG 8). Print-safe CSS, no remote assets.

PROMPT FOR LLM IMPLEMENTATION:
1. Header: tenant logo/name/contact + colors from branding; trek title, dates,
   location, difficulty, duration.
2. Body: per day, a heading with title/description, then a table of ordered
   elements (time, type, title, description). Footer: contact details + page
   numbers via CSS @page.

DEBUGGING:
DEBUG: itinerary_pdf.html trek={{ trek.id }} days={{ itinerary|length }}

OFFLINE DOCKER TEST CASES:
- Rendering produces bytes starting with %PDF and contains every day title.
-->
SEEDEOF
seed_note "app/templates/trekker/itinerary_pdf.html"

cat << 'SEEDEOF' > app/templates/trekker/review_form.html
<!--
FILE PURPOSE: "Leave a Review" screen (UI/UX v2 §3.5; extra file, FLAG 8).

PROMPT FOR LLM IMPLEMENTATION:
1. navbar_trekker shell, centered 560px card: trek name, star rating as radio
   inputs 1–5 (labeled, keyboard accessible), optional comment textarea, submit.

DEBUGGING:
DEBUG: review_form.html booking={{ booking.id }}

OFFLINE DOCKER TEST CASES:
- The form posts to trekker.review_new with a CSRF token and the rating radios
  each have a visible label.
-->
SEEDEOF
seed_note "app/templates/trekker/review_form.html"

cat << 'SEEDEOF' > app/templates/staff/trek_create.html
<!--
FILE PURPOSE: Staff "Create Trek" form (UI/UX v2 §3.4; extra file, FLAG 8).

PROMPT FOR LLM IMPLEMENTATION:
1. Staff shell; same 2-column field layout as admin/trek_form.html but only the
   StaffTrekCreateForm fields. Banner: "Your trek will be pending until an
   Admin assigns an owner and approves it."

DEBUGGING:
DEBUG: staff/trek_create.html rendered

OFFLINE DOCKER TEST CASES:
- Rendered HTML has no status, assigned-staff or available-slots inputs.
-->
SEEDEOF
seed_note "app/templates/staff/trek_create.html"

cat << 'SEEDEOF' > app/templates/email/review_request.html
<!--
FILE PURPOSE: "We'd love your review" email (PDR v2 §14.2).

PROMPT FOR LLM IMPLEMENTATION:
1. Thank the trekker for completing the trek by name; one link to the review
   page (trekker.review_new). Plain, minimal.

DEBUGGING:
DEBUG: review_request.html booking={{ booking.id }}

OFFLINE DOCKER TEST CASES:
- Rendered output contains the review link exactly once.
-->
SEEDEOF
seed_note "app/templates/email/review_request.html"

cat << 'SEEDEOF' >> app/templates/admin/trek_form.html

<!--
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. In EDIT mode only, include shared/_itinerary_builder.html and
   shared/_media_manager.html below the core form (create mode shows a note:
   "Save the trek to add itinerary and photos").

DEBUGGING:
DEBUG: trek_form.html content_sections={{ 'edit' if trek else 'hidden' }}

OFFLINE DOCKER TEST CASES:
- Create mode renders neither partial.
-->
SEEDEOF
seed_note "app/templates/admin/trek_form.html (P10 addendum)"

cat << 'SEEDEOF' >> app/templates/admin/treks_list.html

<!--
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add a "Created by" column and an "Awaiting assignment" quick filter chip.

DEBUGGING:
DEBUG: treks_list.html awaiting_filter={{ request.args.get('awaiting') }}

OFFLINE DOCKER TEST CASES:
- The filter shows only pending, unassigned treks.
-->
SEEDEOF
seed_note "app/templates/admin/treks_list.html (P10 addendum)"

cat << 'SEEDEOF' >> app/templates/staff/trek_manage.html

<!--
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. When trek_service.can_edit_content(current_user, trek), include the two
   shared content partials with the staff content base_url.

DEBUGGING:
DEBUG: staff trek_manage.html editor={{ can_edit_content }}

OFFLINE DOCKER TEST CASES:
- Without treks.edit_content the content sections are absent, not disabled.
-->
SEEDEOF
seed_note "app/templates/staff/trek_manage.html (P10 addendum)"

cat << 'SEEDEOF' >> app/templates/trekker/trek_detail.html

<!--
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Left column additions: media gallery (first image as hero, thumbnails with
   captions/alt), day-by-day itinerary with a "Download itinerary (PDF)" button
   (hidden when there is no itinerary), and a reviews block: average + count and
   the first page of reviews with a "See all reviews" link.
2. Works for anonymous visitors; empty states for no photos/itinerary/reviews.

DEBUGGING:
DEBUG: trek_detail.html media={{ trek.media|length }} reviews={{ review_count }}

OFFLINE DOCKER TEST CASES:
- A trek with no itinerary shows no PDF button; reviewer names show first name only.
-->
SEEDEOF
seed_note "app/templates/trekker/trek_detail.html (P10 addendum)"

cat << 'SEEDEOF' >> app/templates/trekker/browse.html

<!--
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Card cover = first gallery image (placeholder when none), plus average
   rating and review count when reviews exist.

DEBUGGING:
DEBUG: browse.html cover_count={{ treks|selectattr('media')|list|length }}

OFFLINE DOCKER TEST CASES:
- A trek without media renders the placeholder image with alt text.
-->
SEEDEOF
seed_note "app/templates/trekker/browse.html (P10 addendum)"

cat << 'SEEDEOF' >> app/templates/trekker/booking_detail.html

<!--
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. When `can_review`, show a "Leave a review" button linking to
   trekker.review_new; after a review exists show it read-only.

DEBUGGING:
DEBUG: booking_detail.html can_review={{ can_review }}

OFFLINE DOCKER TEST CASES:
- The button is absent unless the booking is completed and unreviewed.
-->
SEEDEOF
seed_note "app/templates/trekker/booking_detail.html (P10 addendum)"

cat << 'SEEDEOF' >> app/templates/shared/sidebar_staff.html

<!--
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. "Create Trek" link only when has_capability('treks.create').

DEBUGGING:
DEBUG: sidebar_staff.html create_trek={{ has_capability('treks.create') }}

OFFLINE DOCKER TEST CASES:
- Staff without treks.create see no Create Trek link.
-->
SEEDEOF
seed_note "app/templates/shared/sidebar_staff.html (P10 addendum)"
log "STEP 6 complete: templates seeded"

# -----------------------------------------------------------------------------
# STEP 7: Tests
# -----------------------------------------------------------------------------
echo "STEP 7: Seeding tests..."

cat << 'SEEDEOF' > tests/unit/test_itinerary_service.py
"""
FILE PURPOSE: Unit tests for services/itinerary_service.py.

PROMPT FOR LLM IMPLEMENTATION:
1. Cover every OFFLINE TEST CASE in the service docstring plus element
   re-sequencing after delete and invalid element_type rejection.

DEBUGGING:
# print(f"[DEBUG] itinerary unit days={[d.day_number for d in days]}")

OFFLINE DOCKER TEST CASES:
- Day numbers and element sequences are always contiguous after any delete.
"""
SEEDEOF
seed_note "tests/unit/test_itinerary_service.py"

cat << 'SEEDEOF' > tests/unit/test_media_service.py
"""
FILE PURPOSE: Unit tests for services/media_service.py.

PROMPT FOR LLM IMPLEMENTATION:
1. Cover every OFFLINE TEST CASE in the service docstring using a tmp
   MEDIA_STORAGE_PATH and generated in-memory images (valid JPEG/PNG/WEBP,
   a fake SVG, an oversized file, a path-traversal key).

DEBUGGING:
# print(f"[DEBUG] media unit key={media.asset_url}")

OFFLINE DOCKER TEST CASES:
- Oversized and non-image uploads leave zero rows and zero files.
"""
SEEDEOF
seed_note "tests/unit/test_media_service.py"

cat << 'SEEDEOF' > tests/unit/test_review_service.py
"""
FILE PURPOSE: Unit tests for services/review_service.py.

PROMPT FOR LLM IMPLEMENTATION:
1. Cover every OFFLINE TEST CASE in the service docstring plus rating bounds
   and the aggregate-average accuracy after several reviews.

DEBUGGING:
# print(f"[DEBUG] review unit avg={avg} count={count}")

OFFLINE DOCKER TEST CASES:
- average_rating always equals the mean of stored ratings at call time.
"""
SEEDEOF
seed_note "tests/unit/test_review_service.py"

cat << 'SEEDEOF' > tests/integration/test_itinerary_pdf.py
"""
FILE PURPOSE: Integration tests for the public itinerary PDF + trek detail.

PROMPT FOR LLM IMPLEMENTATION:
1. Anonymous PDF download (application/pdf, branded with the tenant's name);
   pending trek -> 404; trek with no itinerary -> 404; detail page shows
   gallery/itinerary/reviews; other tenant's trek id -> 404.

DEBUGGING:
# print(f"[DEBUG] pdf test status={resp.status_code} type={resp.mimetype}")

OFFLINE DOCKER TEST CASES:
- No remote asset fetch happens during PDF rendering (network mocked off).
"""
SEEDEOF
seed_note "tests/integration/test_itinerary_pdf.py"

cat << 'SEEDEOF' > tests/integration/test_staff_trek_creation.py
"""
FILE PURPOSE: Integration tests for the staff-created-trek flow.

PROMPT FOR LLM IMPLEMENTATION:
1. Capability gate (403 without treks.create); creation lands pending/
   unassigned; creator edits content but not core fields; Admin sees it under
   "Awaiting assignment", assigns + approves via the existing P2 flow; after
   assignment the assignee gains manage access.

DEBUGGING:
# print(f"[DEBUG] staff trek flow trek={trek.id} status={trek.status}")

OFFLINE DOCKER TEST CASES:
- The staff create route never accepts a smuggled status/assigned_staff_id.
"""
SEEDEOF
seed_note "tests/integration/test_staff_trek_creation.py"

cat << 'SEEDEOF' > tests/integration/test_reviews.py
"""
FILE PURPOSE: Integration tests for reviews end to end.

PROMPT FOR LLM IMPLEMENTATION:
1. Completing a trek triggers review-request emails; review page gating
   (completed + owner + unreviewed); public listing pagination + first-name
   only; average on detail/browse; cross-tenant isolation.

DEBUGGING:
# print(f"[DEBUG] review flow booking={booking.id}")

OFFLINE DOCKER TEST CASES:
- A user's review of a trek they never completed is impossible via any route.
"""
SEEDEOF
seed_note "tests/integration/test_reviews.py"

cat << 'SEEDEOF' > tests/integration/test_trek_content_permissions.py
"""
FILE PURPOSE: Integration tests for the content-edit permission matrix.

PROMPT FOR LLM IMPLEMENTATION:
1. Matrix: Tenant Admin, staff creator, staff assignee, unrelated staff, staff
   without treks.edit_content, trekker, anonymous — against every itinerary and
   media endpoint (admin and staff mirrors); only allowed combinations return
   2xx/302, all others 403/redirect-to-login.

DEBUGGING:
# print(f"[DEBUG] content perm actor={actor} route={route} status={status}")

OFFLINE DOCKER TEST CASES:
- No actor outside the documented rule can mutate any itinerary or media row.
"""
SEEDEOF
seed_note "tests/integration/test_trek_content_permissions.py"

cat << 'SEEDEOF' >> tests/isolation/test_tenant_scoping.py

"""
P10 ADDENDUM — PROMPT FOR LLM IMPLEMENTATION:
1. Add model factories for trek_itinerary_days, itinerary_elements,
   trek_media, trek_reviews; add HTTP isolation cases for /media/<uuid>, the
   itinerary PDF and the reviews listing across tenants.

DEBUGGING:
# print(f"[DEBUG] isolation P10 model={model.__name__}")

OFFLINE DOCKER TEST CASES:
- Every new tenant-scoped model passes read/get/update/delete isolation.
"""
SEEDEOF
seed_note "tests/isolation/test_tenant_scoping.py (P10 addendum)"
log "STEP 7 complete: tests seeded"

# -----------------------------------------------------------------------------
# STEP 8: Commits (left for after code-implementer fills in real logic)
# -----------------------------------------------------------------------------
#git add app/models app/services/itinerary_service.py app/templates
#git commit -m "feat(trek): structured itinerary with PDF export"
#git add app/services/media_service.py
#git commit -m "feat(trek): media gallery"
#git add app/services/review_service.py
#git commit -m "feat(trek): post-completion reviews"
#git add app/staff app/auth
#git commit -m "feat(staff): trek creation pending admin assignment"
#git push -u origin "$BRANCH_NAME"

# -----------------------------------------------------------------------------
# STEP 9: Summary
# -----------------------------------------------------------------------------
echo ""
echo "====================================================="
echo "Milestone P10 (v2) Seeded Successfully!"
echo "-----------------------------------------------------"
echo "Seed files created this run:"
for f in "${SEED_FILES[@]}"; do echo "  - $f"; done
echo "-----------------------------------------------------"
echo "NEXT STEPS:"
echo "1. Copy new keys from .env.example into .env; docker compose build"
echo "   (new Pillow dependency)."
echo "2. Feed these seeds to the code-implementer skill."
echo "3. flask seed-rbac   (adds treks.create / treks.edit_content)"
echo "4. docker compose run --rm test"
echo "5. Open a PR: $BRANCH_NAME -> main once tests pass."
echo "6. Execute P11.sh (Communication Platform)."
echo "====================================================="
log "===== P10.sh (v2) completed successfully ====="

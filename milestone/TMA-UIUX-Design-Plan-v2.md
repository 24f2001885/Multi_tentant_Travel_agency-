# UI/UX DESIGN PLAN v2: TMA Platform — Multi-Tenant Trekking Management SaaS

*Companion to Project Definition Report v2 and Technical Design Report v2. Supersedes the original UI/UX Design Plan for everything described here; any screen or pattern from v1 not mentioned below is assumed carried forward unchanged, now understood as belonging to the **reference tenant frontend** rather than to "the app" as a whole — see §1, Principle 7, for why that distinction now matters.*

**What's actually new here:** this platform now has a shell category that didn't exist in v1 — the **Platform Admin console** — sitting structurally outside every tenant, never reachable from or confusable with any tenant's own Admin experience. It also introduces a genuinely new UI pattern: a Staff member's entire navigation and visible actions are now generated from their *assigned capabilities*, not from a fixed job title, since Tenant Admin builds roles freely rather than choosing from a hardcoded list. Both points are called out explicitly below because they change design decisions that were previously simple (e.g. "what does the sidebar show Staff") into ones that must be computed at render time.

---

## 1. Design Principles

1. **Color carries meaning, not decoration.** Carried forward unchanged as the governing rule for the reference tenant frontend and the Platform Admin console. A tenant's own production frontend may re-skin this entirely from their branding tokens (PDR v2 §5.2) but should keep the underlying discipline: one accent color for primary actions/links/focus, status conveyed only through the badge palette (§6), everything else grayscale.
2. **Role-distinct shells, one shared component library underneath.** Now four shells, not three: **Platform Admin** (its own distinct, non-tenant-branded chrome — see Principle 7), **Tenant Admin/Staff** (dark sidebar, unchanged pattern from v1), and **Trekker** (light top-nav, unchanged pattern from v1) — with the Trekker shell now also serving **anonymous visitors**, since public browsing uses the identical shell minus the authenticated user menu (PDR v2 §5.4). Cards, tables, buttons, badges, and forms stay one shared component set across all shells.
3. **Status is always visible, never implied.** Extended for this phase: every manual booking's sign-off state, every waitlist entry's status, every campaign's draft/tested/sent state, every tenant's active/suspended status, and every custom domain's verification state now get the same always-visible-badge treatment as trek/booking/payment status did in v1 (full table in §6).
4. **Mobile-first, Bootstrap-native.** Unchanged for the reference tenant frontend and the Platform Admin console. A tenant's own independently-built frontend is responsible for its own responsive implementation; this plan does not govern it beyond the accessibility floor in §8.
5. **Density matched to job-to-be-done.** Unchanged principle, now also governing the Platform Admin console, which is the densest surface in the whole platform — a Platform Admin is doing cross-tenant operational triage, so its screens lean further into tables/stat-grids than even the Tenant Admin console does.
6. **The UI is a direct reflection of the permission system, never a parallel thing to keep in sync with it.** New for this phase: a Staff member's sidebar links, visible buttons, and reachable routes are computed at render time strictly from their assigned role's capability set (TDR v2 §4.2, §6) — there is no template that hardcodes "if role == staff, show X." A capability a Staff member doesn't have must result in that nav item, button, or whole screen being absent, not merely disabled, matching the same "absent, not disabled" discipline v1 already used for cross-role action visibility.
7. **The platform boundary is visually absolute.** New for this phase, directly supporting the architectural rule that Platform Admin sits outside every tenant (TDR v2 §2): the Platform Admin console uses a deliberately distinct shell — different base color, different wordmark treatment, never the tenant sidebar component reused with a relabeled badge — so there is no screen state in which a user could plausibly mistake a tenant's own Admin console for the platform operator's control plane, or vice versa.

---

## 2. Wireframe Reference

**Platform Admin (new — operations-console pattern, structurally distinct from Tenant Admin):**
- Fixed left sidebar, same responsive collapse behavior as the Tenant Admin sidebar (240px / icon-only / off-canvas), but rendered in a visually distinct palette from any tenant shell (Principle 7) — a different base hue, not just a relabeled copy of `sidebar_admin.html`.
- Sidebar groups: Organizations (list, onboard new), Platform Financials (net revenue, per-tenant Route breakdown), Monitoring (business + infrastructure load), Backups, Cross-Tenant Audit, Platform Admin's own profile/logout.
- No tenant switcher dropdown implying "acting as" a tenant — Platform Admin views tenant data read-only, in its own screens, never by entering a tenant's own console.

**Tenant Admin & Trek Staff (operations console pattern — unchanged shell from v1, dynamic content):**
- Same fixed sidebar shell, same responsive behavior, as TDR v1/UI-UX v1 specified.
- The nav link *set* is now resolved per logged-in staff member from their assigned role's capabilities, not from a fixed "Staff" vs. "Admin" distinction — Tenant Admin always sees every link (Tenant Admin capabilities are implicitly complete within their own org, short of the protected-capability carve-outs that have no UI surface for anyone to toggle on themselves), while a Staff member sees only the subset their assigned capabilities unlock.

**Trekker & Public/Anonymous Visitor (consumer browsing pattern — now shared, not two separate experiences):**
- Identical Bootstrap top-navbar shell as v1 for both an anonymous visitor and a logged-in Trekker. The only difference: the right-hand side of the navbar shows "Log In / Register" for an anonymous visitor and the user avatar/dropdown for a logged-in Trekker. Every other element — Browse Treks, trek detail, media gallery, reviews — renders identically regardless of auth state, scoped only by which tenant's subdomain the visitor is on.
- Footer (new, sitewide across every shell including public) sits below the main content on every page: copyright/company details, a "Request a Call" link opening a short modal/form, and tenant legal links (Terms, Privacy) pointing at the tenant's own legal text.

---

## 3. Information Architecture (Sitemap)

### 3.1 Public / Shared
- Landing / Home (per tenant, fully public)
- Trek Browse / Search / Filter (public — no login redirect, scoped to the resolved tenant's own treks)
- Trek Detail (public — includes media gallery and reviews)
- Login
- Register (role toggle Trekker | Trek Staff, now with a mandatory, country-code-validated mobile field and a T&C acceptance checkpoint)
- Forgot Password / Reset Password (unchanged from v1)
- Cookie Consent banner (sitewide, dismissible, content supplied by the tenant's legal team)
- Footer (sitewide: copyright, company details, legal links, Request-a-Call form)
- Generic Error page (403/404/500 — unchanged pattern)

### 3.2 Platform Admin *(new — structurally outside every tenant)*
- Platform Dashboard (net Route revenue, per-tenant breakdown, business + infrastructure load at a glance)
- Organizations — list of tenants (status: active/suspended, Route-linkage status, domain)
- Onboard New Organization (name, subdomain, initial branding, first Tenant Admin account)
- Organization Detail (branding preview, domain mapping, Route account linkage, suspend/reactivate)
- Cross-Tenant Audit Log Viewer (with a visible note that this view is itself logged — the meta-audit trail)
- Backups console (trigger/view full-platform or any single tenant's backup; restore)
- Platform Admin Profile / Settings

### 3.3 Tenant Admin
- Tenant Dashboard (stat cards + charts — unchanged pattern from v1, now scoped to this tenant only)
- Trek Management (list/CRUD) — unchanged from v1
- Trek Create/Edit form — now includes the structured Itinerary builder and Media Gallery manager inline (§3.6 cross-reference)
- Staff Management — list, assign a role (not a fixed approve/blacklist action alone; role assignment is now part of this screen)
- **Roles & Capabilities** *(new)* — list of this org's custom roles, create/edit/delete a role, a capability checklist per role (with protected capabilities simply absent from the checklist, never shown as a disabled option)
- User Management — unchanged from v1 (deactivate/reactivate only; Admin still never edits another user's profile fields directly)
- Bookings / Booking Detail — unchanged from v1, Booking Detail now also shows `slot_count`, participant list, coupon applied (if any), and cancellation reason when applicable
- Payments / Payment Detail — unchanged from v1, now also showing Route transfer status
- **Refund Policy settings** *(new)* — the tenant's own day-range → refund-percentage table, editable, pre-filled with the platform default
- **Coupons** *(new)* — list/create/edit discount codes
- **Manual Booking settings** *(new, protected)* — the on/off toggle for the org, visible and editable only by the Tenant Admin, never appearing as an assignable item anywhere in the Roles & Capabilities screen
- **Manual Booking Sign-off Queue** *(new, protected)* — bookings awaiting the Tenant Admin's required sign-off
- **Campaign Composer** *(new)* — compose, build an audience segment, test-send, and send a WhatsApp/email/SMS message
- **Call Requests queue** *(new)* — footer-submitted call-back requests
- Reports & Charts — unchanged from v1
- Audit Log — unchanged from v1 (this tenant's own log; Platform Admin's separate cross-tenant view is §3.2)
- **Backups** *(new)* — this tenant's own self-serve backup/export trigger
- Admin Profile / Settings — unchanged from v1

### 3.4 Staff *(screens are capability-gated — listed here as the full possible set)*
- Staff Dashboard — unchanged shell from v1; its stat cards reflect whichever of finance/trek/ticket data the staff member's capabilities actually grant visibility into
- Assigned Trek Manage / Participants — unchanged from v1
- **Create Trek** *(new — if the trek-creation capability is granted)* — lands unassigned, `pending`, awaiting Tenant Admin assignment and approval
- **Manual Booking creation** *(new — if granted)* — book a trusted person, create their account if needed, mark payment as handled offline
- Tickets (now a multi-turn thread view, same screen location as v1)
- **Campaign Composer access** *(new — if granted; same screen as §3.3's, permission-gated rather than duplicated)*
- Forced Password Change / Pending-Approval Holding Screen — unchanged from v1
- Staff Profile / Settings — unchanged from v1, now including the mandatory mobile field

### 3.5 Trekker
- Trekker Dashboard / Browse Treks — now the logged-in view of the same public browse page (§3.1), differing only in the navbar's user menu
- Trek Detail — unchanged shell, now including media gallery and reviews
- **Group Booking Review / Checkout** *(updated)* — now collects one or more participants per booking, not an implicit single slot; shows coupon-code entry
- Payment / Payment Result — unchanged from v1
- **Waitlist status** *(new)* — shown on a full trek's detail page instead of a disabled Book button; a "You're on the waitlist" state once joined
- My Bookings / Booking Detail — unchanged from v1, Booking Detail now shows GST invoice download, slot/participant count, and — when applicable — the force-cancellation apology notice and guaranteed full-refund confirmation
- **Leave a Review** *(new)* — available only on a completed booking
- Tickets (My Tickets) / Raise Ticket / Ticket Detail — Ticket Detail is now a message thread, not a single response
- **Notification Preferences** *(new)* — per-channel opt-in/opt-out and a view of what the Trekker has consented to (transactional vs. marketing)
- Trekker Profile / Settings — unchanged from v1, now including the mandatory mobile field

### 3.6 Trek Content *(cross-cutting, surfaced inside the Trek Create/Edit flow — not a separate top-level section)*
- Itinerary builder (day-by-day, ordered elements per day)
- Itinerary PDF export (public-facing, linked from Trek Detail)
- Media Gallery manager (multi-image upload/reorder/caption)

---

## 4. Key User Flows

### 4.1 Registration, Login & Password Reset
Unchanged in shape from v1 (Trekker/Staff self-register, Admin-created-staff default-password-plus-forced-reset, non-revealing forgot-password pattern), with two additions applying to every role including Admin: a mandatory, country-code-aware mobile number field at registration, and a cookie-consent/T&C acceptance checkpoint that must be completed before the form can submit, recorded with a timestamp.

### 4.2 Core Transaction Flow (Group Booking + Split Payment + Waitlist Fallback)
1. Visitor — logged in or not — browses/filters open treks and opens Trek Detail.
2. If slots remain: taps **Book Now** → **Group Booking Review** screen collects one or more participants (name, optional age), a coupon code field, and summarizes the per-person and total price.
3. Confirms → a single `pending` booking is created with its full participant set; a Razorpay order is created carrying this tenant's Route split instructions; the Payment screen loads the checkout widget exactly as in v1.
4. On confirmed payment success: slots decrement by the participant count, the booking flips to `booked`, a GST invoice is generated and emailed alongside the existing "Booking Confirmed" notice, and the tenant's Route-linked account receives the split settlement.
5. **If the trek has zero slots remaining:** the sticky booking panel shows a **Join Waitlist** action instead of a disabled Book button. Joining records a waitlist entry; if a slot later frees up (a cancellation, an expired checkout, a confirmation-time race loser), the platform automatically offers it to the earliest waiting entries with a time-limited window to convert it into a real booking, notified through the Trekker's chosen channels.
6. **Race-condition and force-cancellation states** carry forward from v1's Payment Result screen (the "last slot taken" amber state), with one addition: a booking cancelled by Staff/Admin rather than by the customer always shows an explanatory, apologetic notice and a guaranteed full-refund confirmation, visually distinct from the tiered-refund copy a customer-initiated cancellation shows.

### 4.3 Ticket Handling / Support (now multi-turn)
1. Trekker raises a ticket exactly as in v1 (Query or Problem type).
2. Staff/Admin — scoped exactly as in v1 (Staff sees only Problem tickets on their assigned treks; Query tickets remain Admin-only) — opens the ticket and posts a message into the thread rather than a single, final response.
3. The thread stays open: either side can keep replying, each new message appended to the same visible conversation, until the **raising Trekker, and only the raising Trekker**, marks the ticket Resolved.
4. Background purge (10-day Query / 90-day Problem) behaves exactly as in v1, now purging the whole thread, not a single response field.

### 4.4 Tenant Onboarding (new — Platform Admin)
1. Platform Admin completes **Onboard New Organization**: name, subdomain, initial branding (logo, colors, contact details), and creates the first Tenant Admin account (default password, forced reset on first login — same pattern already used for Staff onboarding).
2. The new Tenant Admin logs in, completes their own org's Razorpay Route linkage (KYC, handled through Razorpay's own flow, surfaced in Organization Detail as a linkage-status badge), sets their refund policy (pre-filled with the platform default, editable), and uploads their legal text.
3. Manual/online booking for that tenant should not be presented as available until Route linkage shows **Verified** — the Organization Detail screen's status badge is the single source of truth both Platform Admin and the Tenant Admin check before going live.

### 4.5 Manual / Offline Booking (new)
1. A Staff member with the manual-booking capability (itself only grantable because the Tenant Admin has first turned the org-level toggle on — a protected action Staff can never see or touch) opens **Create Manual Booking**.
2. If the trusted person has no account, Staff creates one inline (name, email, mobile — same validation as self-registration).
3. Staff records the booking and marks payment as handled offline — no payment widget is ever shown on this screen.
4. The booking appears in the Tenant Admin's **Manual Booking Sign-off Queue**, pending until the Tenant Admin reviews and signs off.
5. Once signed off, every other part of the experience — confirmation notice, point-of-contact messaging, trek updates — behaves exactly like an online booking. The booking and its stated value also appear in finance/occupancy reporting, tagged as manual-channel.

### 4.6 Campaign Compose & Send (new)
1. Admin/Staff with the composer capability opens **Campaign Composer**, writes a message using merge-field placeholders (e.g. a recipient's name), and builds an audience segment from available filters (role, booking history, signup date, trek attended, active status) rather than being limited to one trek's participant list.
2. **Test Send**: enters one email and one mobile number; the platform renders the exact message a real recipient would see (using a sample or the composer's own data for the merge fields) and delivers it only to that test destination — no real recipient is touched at this step.
3. Reviewing the test, the composer either edits further or taps **Send**. The real send automatically excludes anyone in the built segment who hasn't consented to that message's category (marketing sends never reach a transactional-only consenter), and the campaign moves to a `Sent` state with a recipient count.

---

## 5. Screen Inventory

*(Full detail already given per-audience in §3; this section is the flat list for quick reference.)*

**Public/Shared:** Landing, Browse, Trek Detail, Login, Register, Forgot/Reset Password, Cookie Consent banner, Footer + Request-a-Call modal, Generic Error.

**Platform Admin:** Dashboard, Organizations list, Onboard Organization, Organization Detail, Cross-Tenant Audit Log, Backups console, Platform Admin Profile.

**Tenant Admin:** Dashboard, Trek list/Create-Edit (with Itinerary builder + Media Gallery), Trek Detail, Staff Management, Roles & Capabilities, User Management, Bookings list/Detail, Payments list/Detail, Refund Policy settings, Coupons, Manual Booking settings, Manual Booking Sign-off Queue, Campaign Composer, Call Requests queue, Reports, Audit Log, Backups, Admin Profile.

**Staff (capability-gated):** Dashboard, Assigned Trek Manage, Participants, Create Trek, Manual Booking creation, Tickets (thread), Campaign Composer (shared screen), Forced Password Change, Pending-Approval screen, Staff Profile.

**Trekker:** Dashboard/Browse (shared with Public), Trek Detail (shared), Group Booking Review, Payment, Payment Result, Waitlist status, My Bookings, Booking Detail, Leave a Review, Tickets (My Tickets)/Raise/Detail (thread), Notification Preferences, Trekker Profile.

---

## 6. Status Badge Convention

All badges keep v1's visual spec (Inter 600, 0.75rem, uppercase, 0.02em letter-spacing, soft-tint background, solid-color text/border only — never a loud filled badge). Every state table from TDR v1/UI-UX v1 (Trek, Booking, Payment ×2 tracks, Staff approval, Ticket) carries forward unchanged. New domains for this phase:

| Domain | State | Color | Hex (text/border) | Soft background |
| --- | --- | --- | --- | --- |
| **Organization status** | Active | Success | `#16A34A` | `#E7F6EC` |
| | Suspended | Danger | `#DC2626` | `#FBE9E9` |
| **Route linkage** | Pending | Warning | `#D97706` | `#FDF3E3` |
| | Verified | Success | `#16A34A` | `#E7F6EC` |
| **Domain verification** | Pending | Warning | `#D97706` | `#FDF3E3` |
| | Verified | Success | `#16A34A` | `#E7F6EC` |
| **Manual booking sign-off** | Awaiting sign-off | Warning | `#D97706` | `#FDF3E3` |
| | Signed off | Success | `#16A34A` | `#E7F6EC` |
| **Waitlist entry** | Waiting | Neutral | `#6B7280` | `#EEEFF1` |
| | Offered | Info | `#0891B2` | `#E5F5F8` |
| | Expired | Danger | `#DC2626` | `#FBE9E9` |
| | Converted | Success | `#16A34A` | `#E7F6EC` |
| **Campaign status** | Draft | Neutral | `#6B7280` | `#EEEFF1` |
| | Tested | Info | `#0891B2` | `#E5F5F8` |
| | Sent | Success | `#16A34A` | `#E7F6EC` |
| **Cancellation reason** *(shown on Booking Detail, not a standalone badge list but a labeled note)* | Customer-requested | Neutral | `#6B7280` | `#EEEFF1` |
| | Company-initiated (force-cancel) | Warning | `#D97706` | `#FDF3E3` |

---

## 7. Style Guide Basics

**Scope note (new for this phase):** the hex/typography tokens below govern the **Platform Admin console** and the **reference tenant frontend** only — they are not a mandate imposed on any tenant's own independently-built production frontend (PDR v2 §5.2, §7). A tenant's own designers are free to use entirely different colors and type as long as the status-badge *meanings* (§6) and the accessibility floor (§8) are respected.

**Reference Tenant Frontend** — unchanged from v1: neutrals, accent (`#1F4FD8`), and semantic badge hues exactly as specified in UI/UX v1 §7.1; Bitter (headings) + Inter (body/UI) typography; 4px spacing scale; `--radius-sm/md/lg` and shadow tiers all carried forward unchanged.

**Platform Admin Console** *(new palette, deliberately distinct per Principle 7)*:

| Token | Hex | Use |
| --- | --- | --- |
| `--platform-sidebar-bg` | `#0B1120` | Platform Admin sidebar — a cooler, darker tone than the tenant sidebar's `#14161A`, so the two are never visually interchangeable even in a screenshot |
| `--platform-accent` | `#7C3AED` | Platform Admin's own accent (violet) — deliberately not the tenant reference app's blue, so a Platform Admin screen is identifiable as such on sight |
| `--platform-accent-soft` | `#F1E9FE` | Active-nav/selected-row tint within the Platform Admin console only |

All other tokens (neutrals, typography, spacing, radius, shadow) are shared with the reference tenant frontend's values — only the sidebar background and accent differ, which is sufficient to satisfy Principle 7 without inventing a second full design system.

---

## 8. Accessibility & Responsiveness

WCAG 2.1 AA remains the target, unchanged from v1, and now applies explicitly to three surfaces independently: the Platform Admin console, the reference tenant frontend, and — as a contractual floor, not a built-and-verified guarantee this plan can certify — every tenant's own independently-built production frontend. Breakpoints, sidebar collapse behavior, trek-card grid responsiveness, table-wrapping, and touch-target sizing all carry forward unchanged from UI/UX v1 §8 for the Platform Admin console and reference tenant frontend. One addition: the cookie-consent banner and the Request-a-Call footer form must both be keyboard-navigable and screen-reader-announced (ARIA live region, matching the existing flash-message pattern) since they now appear on every page, including fully public ones an accessibility audit is most likely to sample first.

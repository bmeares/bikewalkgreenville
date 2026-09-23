# Release 1.22.0 (65) — plan and API contract

Owner: Fable 5.1 (design/orchestration). Implementers: Opus 5.5 subagents, one
work package each. Decisions below were confirmed with Bennett on 2026-09-23.
Everything ships in ONE release. Repo is PUBLIC — no secrets in git.

## Product decisions (confirmed)

- Anyone can browse, route, navigate, record rides and join group rides
  **anonymously**. Submitting anything public (routes, places, reports,
  votes, photos, saved routes/settings sync) requires a **signed-in account**.
- Sign-in is **passwordless**: email → 6-digit code (sent from
  data@bikewalkgreenville.org via the existing walk-audit SMTP config) →
  long-lived Meerschaum token on the device. Username == email. Meerschaum
  user is created with a random password; `attributes.scopes = ['bwg']`.
  Meerschaum **admins** on bwg.mrsm.io are the moderators.
- **Moderation** lives in a web Dash console (`/dash/moderation`, admin-gated,
  same login pattern as trail-counter `plugins/sra/gate.py`) plus "Remove" in
  the app on any contribution for admin accounts. **All photos are hidden
  until an admin approves them.** Text that trips the spam/profanity filter
  is **held** (hidden until approved); clean text goes live immediately.
- **Disclaimer**: full text + checkbox + "I Agree" once per disclaimer
  version; afterwards a one-line compact notice with a "Safety and Liability
  Disclaimer" link is shown every time navigation starts.
- **Group rides**: anonymous, leader + members; join by proximity (within
  1000 ft / 305 m of the leader) OR by share link / 4-letter code.
- **Calendar**: "Upcoming events" card in the menu from the calendar's public
  ICS feed + "Open full calendar" external link; local notifications for
  events in the next few days; Luma links parsed from descriptions.
- **Strava**: GPX export via the system share sheet this release; Strava
  OAuth upload later.
- **Recording rework**: no vertex editor in ride sharing or route drawing.
  Draw route = tap waypoints snapped along the network via `/route`
  (straight-line toggle). Vertex editor survives only as tap-the-corners for
  no-entry polygons.

## Style rules for every agent

- Ponytail: shortest working diff, stdlib/native/existing deps first, no
  speculative abstractions. Mark deliberate shortcuts `# ponytail:` /
  `// ponytail:` with the ceiling and upgrade path.
- Never simplify away: input validation at trust boundaries, auth checks,
  accessibility basics (every icon button has a `tooltip`/`Semantics` label).
- Every non-trivial backend endpoint gets ONE pytest in `tests/`; every
  non-trivial Flutter logic change gets ONE test in `app-native/test/`.
- `flutter analyze` clean and `flutter test` green before you report.
  `pytest tests/` green before you report.
- Do NOT bump the app version, do NOT commit, do NOT deploy. Report the files
  you touched and what you verified.
- Do NOT edit files outside your package's ownership list. If you need a
  change elsewhere, write it down in your report instead.

## Backend API contract (all under bwg.mrsm.io, FastAPI via `@api_plugin`)

Auth header everywhere: `Authorization: Bearer <token>`. Tokens are Meerschaum
API tokens (see `meerschaum/api/routes/_tokens.py`, `meerschaum.core.Token`)
scoped `bwg`, minted server-side by `bwg-auth` after code verification.
Shared dependency `bwg_user(request) -> {'username','is_admin'}|None` and
`require_user` live in `plugins/bwg-auth.py`; other plugins import them with
`from meerschaum.plugins import import_plugins; bwg_auth = import_plugins('bwg-auth')`
(or the equivalent that works in this Meerschaum version — verify).

### bwg-auth (`plugins/bwg-auth.py`)
- `POST /bwg/auth/request-code` `{email}` → `{ok, message}`. Validates
  RFC-ish email (`^[^@\s]+@[^@\s]+\.[^@\s]+$`), lowercases, rate-limits
  (3 codes / 15 min per email, 20 / hour per IP). Stores `sha256(code)` +
  expiry (10 min) + attempts in table `BwgAuth.login_codes` (sql:bwg).
  Sends the code by email (subject "Your Bike Walk Greenville sign-in code").
  Always 200 with a neutral message (no user enumeration).
- `POST /bwg/auth/verify` `{email, code}` → `{token, email, display_name,
  is_admin}`; 401 on bad/expired code (max 5 attempts per code). Creates the
  Meerschaum user on first verify (`User(email, random_pw, type='user',
  attributes={'scopes':['bwg'],'bwg':{}})`, `user.email = email`,
  `conn.register_user(user)`). Mints a token with label `bwg-app <date>`,
  scope `bwg`, expiry 1 year.
- `GET /bwg/auth/me` (Bearer) → `{email, display_name, is_admin,
  disclaimer_version, disclaimer_accepted_at, settings}` where `settings` is
  an opaque JSON blob the app owns (saved places, prefs).
- `PUT /bwg/auth/me` (Bearer) `{display_name?, settings?, disclaimer_version?}`
  → same shape. Display name ≤ 40 chars, filtered by the profanity list.
- `DELETE /bwg/auth/token` (Bearer) → sign out this device (invalidate token).
- Banned users (`attributes.bwg.banned = true`) get 403 on every write.

### map-layers community changes (`plugins/map-layers.py`, keep existing tests green)
- `submit-point`, `community/rollback`, `community/confirm` (renamed below),
  `feedback`: require Bearer (401 otherwise). Store `username` on revisions
  (new column, backfilled null). Legacy `voter` column stays.
- Rollback: non-admins may only roll back their own contributions
  (username match); admins may roll back anything.
- `POST /map-layers/community/vote` `{id, up: bool}` → `{up: N, down: N,
  mine: 'up'|'down'|null}`. One vote per user per contribution; posting the
  same value again removes it (toggle); posting the other flips it. Keep
  `/community/confirm` as a thin alias of `up=true` for old clients. Layer
  properties gain `upvotes`, `downvotes` (replace `confirmations`).
- Photos: revisions gain `photo_status` in `pending|approved|rejected`
  (default `pending`). Photo filenames/URLs are only exposed in layer
  GeoJSON, history and feature responses when `approved`. Serve approved
  photos at `GET /map-layers/photos/{filename}` (404 unless approved).
- Text filter `moderation_check(text) -> 'ok'|'held'` in `bwg-auth` (shared):
  small profanity word list (stdlib only), ≥2 URLs, >50% caps on >20 chars,
  the same text submitted >3 times in 10 min. Held rows get
  `status='held'`; `_active_community` excludes held rows.
- Saved routes: `GET/POST /bwg/routes`, `DELETE /bwg/routes/{id}` (Bearer),
  table `BwgApp.saved_routes` `{id, username, name, from_lat, from_lon,
  to_lat, to_lon, modes, stress, distance_m, duration_min, geometry (GeoJSON
  LineString text), created}`. ≤ 200 per user.
- Transit reach: `TRANSIT_WALK_MAX_M` → 2000 default, `TRANSIT_BIKE_MAX_M`
  5000; both overridable via Meerschaum config
  `plugins:map-layers:transit:{walk_max_m,bike_max_m}`. Investigate why a
  bike+transit request produced "No bus stops within … distance" and fix
  the cause if it is a bug (e.g. access mode not resolved as bike). Error
  message must state the distance searched.

### moderation console (`plugins/moderation.py`)
- `@web_page('moderation')` Dash page, admin-only via Meerschaum session
  (mirror `~/projects/trail-counter/plugins/sra/gate.py` + `staging.py`
  pattern; non-admins get the login page).
- Tabs: **Photos** (pending queue, thumbnail, approve/reject), **Contributions**
  (table: date, user, category, name, status, up/down, held; filters by
  category/status/user/date; actions Remove (reuse rollback logic), Approve
  held), **Users** (list with contribution counts; Ban/Unban), **Export**
  (filters → download GPX, OSM XML `.osm` (negative ids, tags per category:
  route-suggestion/shortcut → `highway=path` + `note` + `source=Bike Walk
  Greenville community app`; points → `amenity`/`hazard` notes), GeoJSON,
  CSV). Exports are for JOSM import — never call the OSM API.
- Also `GET /bwg/moderation/pending-count` (admin Bearer) for the app badge.

### group rides (`plugins/group-rides.py`)
Tables `GroupRides.rides` / `GroupRides.members` on sql:bwg (ponytail: SQL,
not Valkey; ~30 riders × 1 req / 5 s is trivial).
- `POST /group-rides` `{name, rider_name, lat, lon}` → `{code (4 uppercase
  letters, unambiguous set), ride_id, member_id, member_token, share_url}`.
  `share_url = https://bwg.mrsm.io/bwg-app/?ride=CODE`.
- `GET /group-rides/nearby?lat&lon` → `[{code, name, leader_name,
  distance_m, members}]` for rides with leader within 305 m updated < 2 min.
- `POST /group-rides/{code}/join` `{rider_name, lat, lon}` → `{ride_id,
  member_id, member_token, name}`; 404 unknown/ended.
- `POST /group-rides/{code}/ping` `{member_token, lat, lon, heading?,
  speed?}` → `{ended, leader_id, route (GeoJSON Feature|null), members:
  [{id, name, lat, lon, heading, updated, is_leader}]}` — one call pushes and
  pulls. Members silent > 5 min are dropped from the list.
- `PUT /group-rides/{code}/route` (leader token) `{route: Feature}`.
- `POST /group-rides/{code}/leave`, `POST /group-rides/{code}/end` (leader).
- Rides auto-end after 6 h or 30 min of leader silence. Rider names run
  through `moderation_check`; blank → "Rider N".
- `GET /r/{code}` in `plugins/bwg-app.py` → 302 to the share URL.

### events (`plugins/bwg-events.py`)
- Pipe `plugin:bwg-events` metric `events`: fetch the public ICS
  (`https://calendar.google.com/calendar/ical/c_33275914f364c4ba73d477f6ead8495a4e50f057d9e99761fe53ab3076bc406a%40group.calendar.google.com/public/basic.ics`),
  expand recurrences for the next 90 days (`icalendar` + `dateutil.rrule`,
  declare in `required`), upsert `{uid, start, end, title, location,
  description, html_link, luma_url}` (luma: first `lu.ma/...` or
  `luma.com/...` URL in the description).
- `GET /bwg/events.json?days=60` → upcoming events sorted by start.
- Document the prod job: `mrsm register pipe -c plugin:bwg-events -m events`
  + `mrsm start job bwg-events` (`sync pipes -c plugin:bwg-events --loop
  --min-seconds 3600`).

## App work packages (Flutter, `app-native/`)

Sequential on the shared tree (map_screen.dart is 4.2k lines and every
package touches it). Order: A3 → A2 → A1 → A4 → A5.

### A3 — chrome, menu, tour, disclaimer, layers, votes, search
- Record rail icon: red solid circle inside a red ring (custom painter or
  stacked `Icons.circle` + `Icons.circle_outlined`), red always; recording
  state shows a red square (stop) with a pulsing ring. Keep the ● tooltip.
- `modeVerbs` → single "Navigate here" (mode is already on the rail).
- Feature sheet: replace "I rode this — it exists (N)" with two icon buttons
  thumb-up / thumb-down with counts, semantics "Confirm this exists" /
  "Report this is wrong or gone". Uses `POST /community/vote`; when not signed
  in, calls `AuthGate.require(context)` (stub now: a `Future<bool> Function`
  in `lib/auth.dart` that A1 fills in; A3 creates the file with the stub).
- Menu (`tools_screen.dart`): order = Our community map, My rides, Group
  ride (placeholder tile that A4 wires), Settings, **Upcoming events** card
  (from `/bwg/events.json`, next 3 events with date/title/location, tap →
  Luma link if present else html_link; "Open full calendar" →
  https://bikewalkgreenville.org/calendar external), Who Owns The Roads,
  Parking, VRU, bikewalkgreenville.org, then LAST a **Donate** card: title
  "Help Us Build a More Bikeable and Walkable Greenville", body "By donating to
  Bike Walk Greenville, you support this free community tool and our broader
  work to make walking and biking safer, easier, and more accessible for
  everyone.", button → https://bikewalkgreenville.org/donate external.
  Schedule local notifications (flutter_local_notifications, already a dep)
  for events starting within 3 days, once per event uid.
- Welcome tour on first launch (`widgets/welcome_tour.dart`): 4 pages
  (Map & layers, Navigate, Record & share, Community & group rides), page
  dots, "Don't show again" checkbox, Skip/Next/Done; pref `welcome_seen`.
- Disclaimer (`widgets/safety_notice.dart`): `disclaimerVersion = 2`; full
  text below verbatim; checkbox "I have read and agree to the Safety and
  Liability Disclaimer." enables the "I Agree" button; store
  `disclaimer_accepted_version` in prefs (and push to `/me` when signed in —
  A1 wires). Afterwards navigation start shows a compact one-liner under the
  Start button: "Beta: routes may be wrong. Ride at your own risk. Safety and
  Liability Disclaimer" (last phrase tappable → full text sheet).
- Layer groups: `LayerDef.group` ∈ {Community, Biking, Walking, Transit,
  Parking, Safety & advocacy}; layers sheet renders grouped sections with a
  header row per group. Fixed layers unchanged.
- Search: show a progress indicator in the field while a request is in
  flight; results list rebuilds on every state change; fix the web bug where
  tapping a result did nothing (likely the same focus/rebuild race as the
  hamburger fix — `ValueKey`s per result and select on `onTapDown`/pointer
  interceptor; verify on `flutter run -d chrome`).
- Bus-stop error surfaced from the backend message verbatim with an
  "Route without the bus" action.

### A2 — recording rework, record-while-navigating, GPX
- Record button available during navigation (rail shows it always).
  Recording keeps running through navigation start/stop; the nav card shows
  a small red dot + elapsed when recording.
- Stop → `widgets/ride_summary_sheet.dart`: name field (default "Ride on
  <date>"), distance, duration, mini stats; buttons Save, Discard; after
  Save, chips: "Share a stretch", "Export GPX", "Done".
- Trim on the map: two draggable handles (start/end) drawn as circles on the
  ride line; drag snaps to the nearest ride point; the kept stretch draws
  purple, the rest grey. No RangeSlider, no segment chips (multi-segment
  rides: the handle range spans segments; gaps are joined by a straight
  dashed line and noted). "Keep what is on screen" remains as one button.
- Share: kept stretch → RDP to ≤200 vertices → name + optional comment
  sheet → publish as `route-suggestion` via existing submit endpoint. No
  geometry editor.
- Draw route (map-tap sheet): tap waypoints → each new leg fetched from
  `/route` (current mode/stress) and appended; undo last; toggle "Straight
  line" per leg for paths that don't exist; Publish → same name sheet.
- No-entry area: tap corners → polygon, undo, Publish. Delete the remaining
  vertex/curve/pen/erase tooling from `geometry_editor_screen.dart` or
  replace the file with `screens/area_draw_sheet.dart`.
- GPX export: build GPX 1.1 string (stdlib), write to temp file, share via
  `share_plus` (add dep). Also on each ride in My rides.
- `RideRecorder` unchanged in storage format; keep recovery.

### A1 — accounts
- `lib/auth.dart`: `AuthState` provider (token, email, displayName, isAdmin)
  persisted with `flutter_secure_storage` (add dep; on web falls back to
  its web impl). `AuthGate.require(context)` → shows sign-in sheet if
  signed out, returns true when signed in.
- Sign-in sheet: email field → "Send code" → 6 boxes/one field for the code
  → done. Errors inline. Explain in one line why (public contributions carry
  a verified account).
- Settings → Account section: email, display name (editable), "Sign out".
  Settings sync: after sign-in, pull `/me` settings and merge saved places +
  prefs; push on change (debounced).
- Gates: submit point/report/route/area, vote, photo, saved routes.
- Admin: `AuthState.isAdmin` shows "Remove" on ANY contribution and a small
  "Moderation (N pending)" tile in the menu linking to
  https://bwg.mrsm.io/dash/moderation.
- Saved routes: bookmark icon on the route preview card → name → `POST
  /bwg/routes`; "Saved routes" section in the focused-search dropdown (with
  saved places) and in My rides screen tab; tap → re-plan from stored
  params and show.
- `Api` sends Bearer when present; 401 → sign out + re-prompt once.

### A4 — group rides
- `lib/group_ride.dart`: `GroupRideClient` provider: create/join/leave/end,
  5 s ping loop while active (geolocator stream already exists), members
  list, leader route; persists `{code, member_token}` for recovery.
- `screens/group_ride_sheet.dart`: Start a group ride (name, your rider
  name) / Join (nearby list from `/nearby`, code entry) / Active view
  (members with distance-to-you, share link button, leave/end).
- Map: `group-members` symbol layer (name labels, leader highlighted);
  leader's route drawn in a distinct color; "Follow the leader" navigates
  along the leader route (reuse nav with the leader's Feature) and members
  can "Catch up" → routes from me to the leader's latest position.
- Rail: group icon appears while in a ride with member count badge.
- Web: handle `?ride=CODE` in `_openSharedTrip` sibling → join sheet.
- Leader who starts navigation pushes the route via `PUT /route`.

### A5 — accessibility pass
- Every `IconButton`/rail button/chip has a label; `Semantics(liveRegion)`
  for search state, recording state, nav instructions; `MergeSemantics` on
  list tiles; min 48 dp tap targets; contrast check of purple/red/green on
  light + dark; `excludeSemantics` on decorative map controls; focus order
  in sheets; test with `flutter test` semantics finders + document a
  TalkBack/VoiceOver checklist in `docs/wiki/ACCESSIBILITY.md`.

## Disclaimer text (verbatim)

Title: Beta App Safety and Liability Disclaimer

This app is a beta navigation tool intended for informational purposes only. Routes and conditions are based on OpenStreetMap data, user-submitted information, and other sources that may be incomplete, inaccurate, outdated, or unverified. Suggested routes are not guaranteed to be safe, accessible, lawful, passable, or appropriate for your abilities or mode of travel.

Always use your own judgment, remain aware of your surroundings, obey all signs and laws, and avoid interacting with the app while moving. Actual conditions—including traffic, construction, closures, surface hazards, weather, lighting, and accessibility—may differ from those shown. Do not use the app for emergencies.

By selecting “I Agree,” you acknowledge that bicycling and walking involve inherent risks and that you use this app and any suggested route at your own risk. To the fullest extent permitted by law, Bike Walk Greenville, its officers, employees, volunteers, contractors, contributors, and data providers are not responsible for injuries, losses, damages, or other consequences arising from your use of or reliance on the app.

Checkbox: I have read and agree to the Safety and Liability Disclaimer.

## Release steps (orchestrator)
1. Version 1.22.0+65 in `app-native/pubspec.yaml`; release notes ≤ 500 chars.
2. `flutter analyze`, `flutter test`, `pytest tests/`.
3. Build AAB + APK + web; deploy plugins + web per HANDOFF split procedure.
4. Prod: register `bwg-events` job; ensure `Users` table has admins.
5. Play beta via `gplay release … --track beta`; TestFlight via mac.
6. Commit + push main; update HANDOFF.md.

## As-built contract notes (backend done 2026-09-23; app agents build against THESE)

- `POST /bwg/auth/request-code`: 400 bad email (no `+` allowed, ≤60 chars), 429 on rate limit, else 200 `{ok, message}`.
- `POST /bwg/auth/verify` → `{token, email, display_name, is_admin}`; 401 bad/expired.
- `GET/PUT /bwg/auth/me` → `{email, display_name, is_admin, disclaimer_version, disclaimer_accepted_at, settings}`.
- `DELETE /bwg/auth/token` signs out this device.
- `POST /map-layers/community/vote {id, up}` → `{ok, up, down, mine, confirmations}`. The layer GeoJSON carries `upvotes`/`downvotes`; the app only learns `mine` from the vote response.
- `submit-point` → `{…, status: 'held'|'published', photo_status}`; photos limited to jpg/jpeg/png/webp/heic. Held text is invisible until an admin approves it: the app should say "Thanks — your submission is awaiting review" when `status == 'held'`.
- `community/history` rows gain `mine` (bool, signed in) and `photo_url` (approved only). Non-admins can roll back only their own (403 otherwise).
- `POST /map-layers/community/moderate` (admin) `{id|ids, status?, photo_status?}`.
- Saved routes: `GET /bwg/routes` → `{routes:[…]}` newest first (`geometry` parsed GeoJSON); `POST` → the saved route, 409 at 200; `DELETE /bwg/routes/{id}` → `{ok}` / 404.
- All community writes require Bearer; 401 → app must sign out and re-prompt once.
- Group rides: `/ping` on an ended ride → 200 `{ended: true}`; leader leaving ends the ride; leave cancels the token; codes use alphabet `ABCDEFGHJKMNPRTUVWXY`.
- Events: `GET /bwg/events.json?days=1..90` → `{events:[{uid,start,end,all_day,title,location,description,html_link,luma_url}]}` (UTC ISO).
- Admins = Meerschaum users of type `admin` on the API instance whose username is their email (promote after first sign-in).

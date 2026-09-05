# Handoff — work continues on host `omega` (repo at `~/projects/bikewalkgreenville`)

Updated 2026-08-07 (twelfth session). Read this + `DATA.md` before touching
anything.

## ⚠️ Repo is PUBLIC on GitHub

`gh repo view bmeares/bikewalkgreenville` → PUBLIC. **Never commit secrets** — no SMTP passwords, no keystores, no connection strings. `.gitignore` covers `.env`, `keystore.properties`, `*.keystore`, `*.jks`. Plugin config in `mrsm-compose.yaml` interpolates from the environment (`$MRSM_SMTP_*`); real values live only in the untracked `.env` and in the prod container's config.

## State (all TESTED; backend DEPLOYED)

- **`app-native/`** — native Flutter app (map-first, MapLibre GL), v**1.5.0+35**, applicationId `org.bikewalkgreenville.app`. `flutter analyze` clean, `flutter test` 27/27 green, signed release APK + AAB built on omega (signer SHA-1 `537F9A88…A843`, the shared SRA upload key).
  - `build/app/outputs/flutter-apk/app-release.apk`
  - `build/app/outputs/bundle/release/app-release.aab`
- **Backend live on bwg.mrsm.io**: `plugins/walk-audit.py`, `plugins/map-layers.py` (v0.5.0 — edge-snap routing + multi-modal, see below), `plugins/bike-parking.py`, `plugins/gtfs.py`, `plugins/bcycle.py`.
- **Prod jobs registered** inside `mrsm-api-bwg-1`: `transit` (daily GTFS sync) and `bike-parking` (daily Overpass sync), alongside `annex-watch`, `trails-output`, `duke`, `who-owns-the-roads`, `parking`. Both verified running with successful first syncs (`mrsm show logs <job>`).
- **walk-audit config** moved off env vars onto Meerschaum config (`plugins:walk-audit:{smtp,notify}`). Prod values live in the container volume at `/meerschaum/config/plugins.json` (chmod 600); the `/meerschaum/.env` hack has been **deleted**.
- `WalkAudit.reports` is empty (the deploy-check row was removed).

### 2026-09-05 night (omega) — app v1.21.2+64, map chrome cleanup (no backend change)

- **Web: hamburger opened Directions.** The search field's suffix `Row` gains a
  "clear" button when the field has focus; on web the mousedown focuses the
  field mid-tap, the row rebuilds with one more child, and UNKEYED elements are
  reused by index — the hamburger's in-flight tap fired on the element that
  now held the Directions button. Fix: `ValueKey`s on the three suffix buttons.
- **Travel modes** left the top column (overflowed at large text) for a rail
  button (current mode icon, above Layers) that opens `TravelModes` in a sheet.
- **Map tap sheet** shows only actions AT the spot: `Bike here` + tune, and
  wrapping chips Report / Add place / Draw route / No-entry area / Who owns
  this road?. "Community edits & history" and "My rides" left it (both live in
  Dashboards & Tools; rides also via the ● rail button).

### 2026-09-05 evening (omega) — app v1.21.1+63, map-layers v0.20.1 batch removal + web icon

- **Why only the newest edit was removable**: the history list shows the undo
  icon only on ACTIVE rows (heads of edit chains), and a rollback reverted one
  revision, resurrecting its predecessor ("peel one layer"). Now
  `POST /community/rollback` takes `ids: [...]` (≤200, one request = one
  rate-limit hit) and reverts each active id **plus its whole `replaces`
  chain**; returns `{ok, removed, skipped}`. Tests updated
  (`test_community_api.py`: chain removal, batch, 409 only when nothing valid).
- App: Community edits AppBar ☑ toggles select mode (checkboxes on removable
  rows, bottom "Remove N" → one reason → `Api.rollbackContributions`). The map's
  "Remove this contribution" now removes the whole chain too (copy already said so).
- Web icons: `web/favicon.png` + `icons/Icon-192/512` are the BWG logo masked to
  an iOS-style squircle (superellipse n=5, transparent corners); maskable
  variants stay square on white inside the 80 % safe zone. Generated with PIL
  from `ios/.../Icon-App-1024x1024@1x.png`.

### 2026-09-05 later (omega) — app v1.21.0+62, map-layers v0.20.0 "Prefer community routes"

**DEPLOYED 2026-09-05 12:40 ET**: map-layers v0.20.0 + web bundle 1.21.0+62 live on
bwg.mrsm.io (`/bwg-app/version.json` = 62; `/map-layers/route` echoes `community`
and `alternatives[*].climb_ft`; `/community/confirm` answers 400 on an empty body).
Container backup of the previous plugin + web bundle:
`/meerschaum/bwg-release-backups/61/`. Play: versionCode 62 on **internal**
(completed) and **beta** (in review). TestFlight: build 62 uploaded from
`mac:~/projects/bwg-release-62` (Delivery 530df3ff…); notes/group/submit run
once processing finished. Play release notes must stay ≤ 500 chars
(`app-native/play-release-notes.json`, 461). Source NOT yet committed at deploy
time — commit `main` next.

- Mirror of the trail preference for rider-drawn edges. Community
  `shortcut`/`route-suggestion` rows already entered the graph as `path`;
  they now carry `extras[5] = True` (chunk_extras grew a 6th element) and
  `_astar.edge_weight` multiplies them by `COMMUNITY_PREF_FACTOR` (0.5) when
  `_COMMUNITY_PREF` (contextvar, default on, `?community=0`) is set. Joins the
  plan cache key and `min_factor` (heuristic stays admissible); response echoes
  `community`. Test: `TestCommunityPreferenceToggle` (3x dogleg: 0.4 loses,
  0.2 wins).
- App: `preferCommunity` setting (`prefer_community`, default on) in Settings
  and the planner's "More" (per-trip override `_tripCommunity`, dies with the
  trip like `_tripTrail`); `Api.route(community:)` sends `community=0` when off.
- **Community badge on routes**: legs carry `community`; `_community_ranges(legs)`
  → `properties.community_ranges` (also merged with offsets in the transit and
  bcycle composites). App: `NavRoute.communityRanges`/`communityNames`; the
  hazards ("What to expect") sheet lists the rider-drawn routes used and a
  tapped stretch says "Community route: <name>". Test `TestCommunityRangesOnRoute`.
- **Confirm-it-exists votes**: `POST /map-layers/community/confirm` `{id, voter}`;
  `confirm` rows live in `community_revisions` (`confirms`, `voter` columns —
  `_community_rows` now `SELECT *` and backfills them, `_active_community`
  skips them). Layer property `confirmations`; history `type: 'confirm'`.
  App: `AppState.voter` (random 32-hex, `voter_id`), `confirmed_ids`; feature
  sheet button "I rode this — it exists (N)". Test in `test_community_api.py`.
- **Route around open reports**: `_open_report_points()` (community
  access-issue/crossing points + walk-audit reports minus dismissals, direct
  SQL on `WalkAudit.reports`/`report_edits`) → 25 m buffers; chunks touching
  one get danger ×3 at graph build (`REPORT_PENALTY_FACTOR`). Walk-audit
  reports only take effect at the next graph build (community edit or 24 h
  TTL). Test `TestOpenReportsRepelRoutes`; `RouteGraphTestCase._graph`
  stubs `_open_report_points` (set `self.report_points`).
- **Share trip link**: route card share icon copies
  `https://bwg.mrsm.io/bwg-app/?from=lat,lon&to=lat,lon&stress=…` to the
  clipboard (no share_plus dep — add it if a native share sheet is wanted);
  the web build reads `Uri.base` query in `_openSharedTrip()` after the first
  style load and plans the trip. Android/iOS app links are NOT registered.
- **UX plan slices (Codex × Fable, `docs/planning/FLUTTER_UX_PLAN.md`,
  mailbox `docs/planning/ux-review/`)**: (1) **Saved places** —
  `AppState.savedPlaces` (`saved_places` prefs key, identity = 5 dp coords),
  bookmark toggle on the place card, saved rows above recents in the
  focused-search dropdown (bounded to 35 % height, scrollable, tap routes
  immediately, ⋮ Rename/Remove with snackbar Undo); `test/saved_places_test.dart`.
  (2) **Route choices at a glance** — `alternatives[*].climb_ft` from the
  backend; `alternativeDelta`/`extraWarnings` in `nav.dart`; alternative chips
  show `name · +4 min · −80 ft` deltas (unknown climb is null, never 0) and a
  gap glyph only for warning kinds the selected route lacks; chip is
  `widgets/alternative_chip.dart` (one semantic node with label + tap action,
  `test/alternative_chip_test.dart`). (3) Recording recovery (Codex) —
  segmented `points` + `segment_starts` + `active_ms`, `ride_in_progress`
  checkpoint (30 s / 50 pts, injected timer), pause/resume with lifecycle-only
  auto-resume, isolated bad fixes ignored, >15 s gap splits, delete undo,
  `widgets/recording_sheet.dart`. All three cross-reviewed via
  `docs/planning/ux-review/`; NO device/screen-reader validation yet.
- **Ride recording** (`lib/rides.dart`, `screens/rides_screen.dart`):
  `RideRecorder` (provider in main.dart) records a 1 Hz GPS trace while the
  app is on screen (wakelock on; foreground only — the manifest strips
  FOREGROUND_SERVICE on purpose), keeps ≥3 m steps, drops fixes with
  accuracy >30 m, saves to SharedPreferences `rides`. Rail ● button starts/
  stops; stopping opens the ride on the map (purple `ride` source/layer) with
  a trim sheet: RangeSlider start/end, "Keep what is on screen" (longest run
  inside the viewport), "Share this stretch as a community route" →
  `fitToVertexLimit` (RDP to ≤200 vertices) → `GeometryEditorScreen`
  (category route-suggestion) → publishes via the existing submit endpoint.
  "My rides" lives in the long-press menu and Dashboards & Tools (Tools pops
  the picked ride back to the map). Test `test/rides_test.dart`.

### 2026-09-05 (omega) — app v1.20.1+61, map-layers + walk-audit NaN fix (DEPLOYED, Play beta + TestFlight)

- **`/map-layers/community/history` 500'd in prod**, so the Community edits page
  showed "History could not load" — and roll back (the only delete) lives on that
  page. Cause: `df.where(df.notna(), None)` leaves **NaN** in an all-null column
  (Postgres reads all-null `replaces`/`reverts` back as float64), and NaN is not
  JSON. Fixed with `df.astype(object).where(...)` in `_community_rows()`
  (map-layers) and `_rows()` (walk-audit — same latent bug). Regression test:
  `tests/test_community_api.py::test_history_serializes_all_null_columns`
  (its `NaNPipe` forces the float column; the test fails without the fix).
- **Delete from the map**: the feature sheet now has "Remove this contribution"
  for community features (askReason → `rollbackContribution` → `_refreshCommunity`),
  so removing no longer requires the history list.
- **`community-areas` fill filter** is now `all(geometry-type == Polygon,
  category == 'no-entry')` — a drawn route can never shade as keep-out.
- Verified: `/map-layers/community/history` 200, `/walk-audit/history` 200,
  rollback of an unknown id → 409, `/bwg-app/version.json` = 1.20.1+61.
  Play **beta** versionCode 61 live; TestFlight build uploaded from `mac`
  (`~/projects/bwg-release-61`).
- Open: the Springer St tunnel contribution is stored correctly as a
  `LineString`/`shortcut` (verified in `MapLayers.community_revisions`), and only
  `no-entry` **polygons** exclude routing — if shading still shows there it is
  the amber tap highlight (12 px, 0.55 opacity) or the parking land-use fill
  (5 of its polygons overlap that corridor), not the contribution.

### 2026-08-10 latest (twentieth session, omega) — Flutter WEB build, bwg-app plugin v0.1.0 (DEPLOYED)

The app now also ships as a Flutter web build, served from prod at
**https://bwg.mrsm.io/bwg-app/** (embeddable from bikewalkgreenville.org, same
pattern as Who Owns The Roads — `/dash/app` is a thin full-viewport iframe
page via `@web_page`). `flutter test` 79/79, analyze clean, prod curl + headless-
chromium verified (map, layers, pins all draw; layer GeoJSON loads cross-origin).

1. **`app-native/web/`** added (`flutter create --platforms web .`).
   `index.html` carries the maplibre-gl JS/CSS `<script>`/`<link>` tags —
   maplibre_gl_web 0.26.x REQUIRES them (README "Web" section); without them
   the map is a blank div. All existing plugin deps have web implementations
   (maplibre_gl_web, geolocator_web, image_picker_for_web,
   flutter_local_notifications_web) — no code changes were needed.
2. **Build**: `flutter build web --release --base-href /bwg-app/` → 31 MB
   `build/web/`. The base-href MUST match the mount path.
3. **`plugins/bwg-app.py`** (v0.1.0): `@api_plugin` mounts FastAPI
   `StaticFiles(html=True)` at `/bwg-app` from `<MRSM root>/bwg-app-web/`
   (skips the mount if the dir is absent, so dev environments don't 500);
   `@dash_plugin`/`@web_page('app')` adds the iframe page with
   `allow="geolocation; camera"`.
4. **Deploy** (bundle is NOT in git — build/ is ignored; redeploy after each release):

   ```bash
   cd app-native && flutter build web --release --base-href /bwg-app/ && cd ..
   rsync -e 'ssh -p 2269' -a --delete app-native/build/web/ meerschaum@mrsm.io:/tmp/bwg-app-web/
   scp -P 2269 plugins/bwg-app.py meerschaum@mrsm.io:/tmp/
   ssh -p 2269 meerschaum@mrsm.io \
     'docker exec mrsm-api-bwg-1 rm -rf /meerschaum/bwg-app-web && \
      docker cp /tmp/bwg-app-web mrsm-api-bwg-1:/meerschaum/ && \
      docker cp /tmp/bwg-app.py mrsm-api-bwg-1:/meerschaum/plugins/ && \
      docker restart mrsm-api-bwg-1'
   ```

5. Web-specific behavior notes: geolocation needs the HTTPS origin (fine on
   bwg.mrsm.io) and prompts per-browser; `myLocationRenderMode` /
   `setAttributionButtonMargins` log "not available in web" (harmless);
   TTS/notifications degrade gracefully. The Dio client's prod-first base
   URL is same-origin from `/bwg-app/`, so API calls just work.
6. **iOS**: possible but not set up — the repo has no `ios/` dir. On a macOS
   host: `flutter create --platforms ios .` in `app-native/`, add
   `NSLocationWhenInUseUsageDescription` to Info.plist (maplibre_gl README),
   then `flutter build ipa` with an Apple signing identity.
7. `android/gradle.properties` JDK pin updated for the current omega:
   `org.gradle.java.home=/usr/lib/jvm/java-21-temurin-jdk` (the old
   `~/sdk/jdk21` path no longer exists).

### 2026-08-08 earlier (nineteenth session, omega) — app v1.17.0+51, map-layers v0.16.0

Bennett round 4: Furman College Way should route like the trail (car-free
SRT access between the two roundabouts); direct McHan → Legacy Park still
crossed onto S Church St where Church rides the embankment (steps said
Wakefield; the crossing doesn't exist — TIGER digitized it); parking lots as
connection stop-gaps; trail pref must shape Quiet/Balanced/Direct and live
under the preview's "More"; dark basemap hides too much detail → light is
the default theme; assorted layer UX (below). `pytest` 76/76, `flutter
test` 79/79, analyze clean, seeded 300-trip sweep re-run.

**Backend (map-layers v0.16.0):**

1. **`TRAIL_TIER_STREETS`** — rows whose suffix-stripped name matches
   (`FURMAN COLLEGE`) are re-categorized `'srt'` at graph build; `_astar`
   prices by category at query time, so the bias holds for every mode and
   stress, and `?trail=0` neutralizes it with the trail.
2. **`GRADE_SEPARATED_ROWS`** (Wakefield, Judson) — the Church St embankment
   severs the side streets next to the Springer tunnel; matching rows get
   the vertices inside the window CLIPPED OUT (street survives both sides,
   the crossing is fiction). Unlike TUNNEL_ROOF_ROWS this splits rather
   than drops.
3. **`_crosses_grade_separation` guard in `_add_connector`** — with
   Wakefield gone, the route escaped via a junction connector: the Church
   bike lane's chunk end sits 10 m from the tunnel's WEST portal (on the
   bridge) and got ramped down onto Springer. No junction/stitch connector
   may bridge a tunnel-roof or grade-separated window now; T-namespaced
   portals stay exempt.
4. **Springer CUSTOM_PATHS realigned to the real lot drive** (OSM ways
   339268048/365924783) and the drawn line now starts at the WEST portal so
   the shortcuts layer connects to Springer St. New per-entry
   `route_coords`: what joins the graph (east-of-Church only — surface
   coords under the bore would re-fuse with Church, the v0.13 lesson) vs
   the `coords` that draw. "Parking lots as stop-gaps" = keep tracing lot
   drives into CUSTOM_PATHS; graph-wide OSM `parking_aisle` ingest was
   scoped (10.2k ways in bounds — needs its own perf pass) and skipped.
5. **parking-garages endpoint clamps counter glitches** — Church St. Garage
   reported −535 occupied ("1484 of 949 spaces open"); occupancy outside
   [0, capacity] now drops the occupancy/availability fields.
6. walk-audit v0.2.0 grew a `missing-shortcut` category ("Suggest a
   shortcut (tunnel, path, cut-through)") — rider-suggested shortcuts
   arrive as ordinary moderated reports; graduate the good ones into
   CUSTOM_PATHS.

Verified on the LOCAL graph build (live sql:bwg data) pre-deploy:
McHan → Legacy Park now rides Fred Garrett → University St → Furman College
Way → SRT at **direct, balanced and quiet** (zero Church steps); McHan →
Briar (south end) still rides the tunnel + the new lot path; trail=0 direct
takes Pearl/Cleveland/McDaniel with no impossible turns.

**App (v1.17.0+51):**

7. **Trail pref applies to every stress level** (`_planTrip` sends
   `trail=0` whenever the pref/override is off) and the planner ("More")
   always shows the switch — reworded "Prefer the Prisma Health Swamp
   Rabbit Trail" (Settings toggle matches).
8. **Light theme is the default** (`ThemeMode.light` initial + load
   fallback): no keyless dark vector style carries more detail than
   OpenFreeMap `dark` (liberty/bright/positron are light; fiord failed in
   v1.13–15), so instead of a worse map the app defaults to the detailed
   one. "Dark app and dark map" subtitle removed.
9. **LayerDef grew `fixed` / `advocacy` / `dashed` / `filter` /
   `lightBaseColor`**:
   - landmarks + custom-paths are `fixed`: always drawn, no sheet toggle
     ("Trail landmarks" and "Shortcuts & tunnels" toggles are gone);
     shortcuts draw DOTTED (`lineDasharray [0.5, 2]`).
   - **Settings → "Experimental advocacy layers"**: bike-stress, parking
     garages, downtown parking land use (renamed), VRU ×3, street lights.
     Opting in adds the toggle to the layers sheet AND switches the layer
     on (`AppState.setAdvocacyLayer`, persisted `advocacy_layers`); the
     stress legend only shows while bike-stress has a sheet toggle.
   - **Crash history → "Vulnerable road users", split three ways** over
     the same GeoJSON: `vulnerable-heat` (heatmap), `vulnerable-crashes`
     (circles, `filter killed == 0`), `vulnerable-fatalities` (pins,
     `filter killed > 0`).
   - **street-lights readable on light base**: `lightBaseColor #A66A00`
     replaces the amber when the basemap is light.
10. **Tap a red/orange stretch → the sheet says why**:
    `NavRoute.segmentNotes(atM)` (unit-tested) matches the tapped
    distance against hillRanges (grade % + shade word) and warnRanges
    (no bike lane / no sidewalk, "drawn dashed red"); rows appear in the
    route-segment sheet.

Device test (v1.17.0+51): fresh install opens LIGHT; Settings → Dark has no
subtitle; route drawn → More → trail switch present at every stress, off
reroutes away from the SRT on Direct too; direct McHan → Legacy Park never
touches Church (rides Furman College Way + trail); shortcuts layer is a
dotted line that meets Springer St and follows the lot; landmarks show with
no toggle; Settings → Experimental advocacy layers → enable Street lights
(brown dots on light map), VRU heat/crashes/fatalities as three toggles;
parking garages pins draw and Church St. Garage shows no nonsense count;
Report → "Suggest a shortcut" category submits; tap an orange stretch of a
route → sheet explains the grade; tap a dashed-red stretch → sheet names
the missing infrastructure.

### 2026-08-08 earlier (eighteenth session, omega) — app v1.16.0+50, map-layers v0.15.0 (DEPLOYED)

Bennett round 3: navy abandoned ("too hard to see the bike lanes" — back to
black); Springer St does NOT meet Church St at grade (it tunnels under, yet
direct routes turned left onto Church from Springer); Paperclip label only
at close zoom; Trail chip out of quick settings (trail preference only
shapes Quiet); climb text off the route card; tapping the route line should
say which street that stretch is. `pytest` 72/72, `flutter test` 74/74,
analyze clean, sweep re-run post-change.

**Backend (map-layers v0.15.0) — the tunnel/Church fusion, TWO mechanisms:**

1. **Tunnel node namespace.** The graph is 2D and the 12 m grid snap fused
   the OSM Springer tunnel way's geometry with the S Church St bike-lane
   edges crossing ABOVE it (verified live: one node carried both). Tunnel
   rows (OSM street rows, tuple element 8 `tunnel=True`) now key their
   nodes into a `('T', …)` namespace — they can never share a cell with
   surface geometry — and rejoin the network ONLY through portal
   connectors to the nearest chunk endpoint of a street with the SAME
   suffix-stripped name (`_street_base`: "Springer Street" == "SPRINGER
   ST" == "Springer St"). Junction connectors also never target tunnel
   chunks (`u[0] != 'T'`).
2. **TUNNEL_ROOF_ROWS.** PCC's Springer stress stub is a 2-vertex straight
   line crossing Church on the surface — the roof of the tunnel digitized
   as a street. Vertex-in-bbox tests MISS it (both endpoints sit outside
   any between-the-portals box); `_tunnel_roof` does segment-bbox overlap
   and drops matching parts at ingest (both stress and gap-fill loops).
   The offending node had been minted by the junction pass SPLITTING that
   chunk where Church's lane end projected onto it.
   Verified: zero nodes in the portal window carry both a SPRINGER and a
   CHURCH edge; McHan → Briar still rides the tunnel; direct McHan →
   Legacy Park reaches Church via Wakefield St (a real intersection).

**App (v1.16.0+50):**

3. **Dark theme back to black**: basemap `dark` (fiord navy swallowed the
   bike-lane greens), Material dark = stock seeded near-black scheme; high
   contrast = true black + white inks. All `_navy*` consts deleted;
   `brandOnSurface` dark keeps the light leaf (`_leafOnDark`).
4. **Paperclip label minZoom 13.5** (landmarks LayerDef).
5. **Trail preference only shapes QUIET routes**: `_planTrip` sends
   `trail=0` only when stress==quiet and the pref/override says off;
   balanced/direct always ride the server's stock weighting. Trail chip
   REMOVED from the preview quick row (chips are now Quiet/Balanced/Direct
   + More); the planner's trail switch only shows when Quiet is selected
   and is reworded "Prefer trail routes — choose routes that ride the
   Prisma Health Swamp Rabbit Trail, even when a street way is shorter"
   (Settings toggle reworded to match).
6. **Route card is distance + ETA only** (climb lives in the hazards sheet
   with the elevation graph).
7. **Tap the route line → segment info**: `_onMapClick` snaps the tap with
   `NavProgress.of` (<30 m) and opens a sheet naming the street for that
   stretch (step name), how far the route rides it, its instruction, and a
   "Who owns this road?" row into the existing road-info sheet.

Device test (v1.16.0+50): dark mode = black map + black chrome, bike lanes
pop; Paperclip label appears only zoomed in past ~13.5; route drawn → quick
row has NO trail chip; planner shows the trail switch only with Quiet
selected; card shows "3.2 mi · 25 min" with no ↑ ft; tap mid-route → sheet
names the street; direct-stress route near the tunnel NEVER turns onto
Church St from Springer.

### 2026-08-08 earlier (seventeenth session, omega) — app v1.15.0+49, map-layers v0.14.1 (DEPLOYED)

Bennett's follow-ups on v1.14: navy still too low-contrast; base picker must
not offer a dark map on light chrome (or vice versa); Paperclip marker too
far south + should be a LABEL not a pin; search X sometimes leaves the
recents list up; and the two trip flows (search→"Bike here" vs the planner
sheet) confuse people — prefs are hard to find and the planner opens with an
empty destination. `pytest` 68/68, `flutter test` 74/74, analyze clean.

**Backend (v0.14.1)**: LANDMARKS Paperclip moved onto the trail line at the
top of the switchbacks — 34.8509, -82.3834 (was 34.8487, ~240 m south of the
geometry). Nothing else.

**App (v1.15.0+49):**

1. **Dark theme contrast rework** — the fix was SPREAD, not darkness:
   background stays deep (0xFF0C1626) but container tiers now step up ~7
   luminance points each (low 0xFF16243C … highest 0xFF334A74) so cards and
   sheets separate; inks near-white (onSurface 0xFFF1F4FA, variant
   0xFFC5CFDF, outline 0xFF8FA0B8); primary leaf brightened (0xFFB2D488,
   `_leafOnNavy`, also `brandOnSurface`'s dark value).
2. **Base picker is Standard / Satellite.** Standard = follow the theme;
   forced light/dark bases are GONE (mismatched chrome/basemap read as a
   glitch). Persisted `map_base` light/dark values migrate to `auto` in
   `AppState.load()`. Settings → Appearance is now the only light/dark
   switch.
3. **Landmarks draw as text labels** (`LayerDef.isLabel`): `renderLabel()`
   in map_icons.dart paints bold-italic name bitmaps with a halo (ink picked
   for the base — light text on dark/satellite), one `addImage` per feature,
   symbol layer `iconImage: ['get','__img']` (property injected client-side).
   Bitmaps because the satellite style has NO glyphs — a real text layer
   renders nothing there.
4. **Search X now also unfocuses** — with focus kept, the recents list
   re-appeared under the cleared field (the "doesn't clear sometimes"
   report was focus surviving the trip-planner round-trip).
5. **Trip flows unified around the route preview**:
   - `_tripPrefsRow()` on the preview (bike/walk/roll plans): Quiet /
     Balanced / Direct chips (write the durable stress pref, same as
     Settings), a **Trail** chip (per-trip only, `_tripTrail`), and a
     **More** chip into the planner. Every chip replans instantly (silent —
     the line just redraws). Chips are 38 dp — gloved-thumb-sized.
   - **The planner always opens with the destination you're looking at**:
     `_openDirections()` falls back active-trip `_to` → searched `_place` →
     empty. The "search again inside the planner" friction is gone.
   - **Planner gains the per-trip trail switch** (returned via
     `DirectionsResult.trail` → `_tripTrail`; Settings default untouched,
     override dies with `_clearRoute`). Pick-on-map round-trips keep it.
6. Directions sheet's my-location icon was `Colors.black45` — invisible on
   navy; now `onSurfaceVariant`.

Device test (v1.15.0+49): dark mode — cards/sheets visibly layered, text
crisp; layers sheet shows two base pills only (Standard/Satellite), theme
toggle flips the standard base; "The Paperclip" renders as italic text ON
the switchbacks (both bases), still searchable; search something → X →
recents gone; search a place → tap the search-bar directions arrow → To is
prefilled; route drawn → chips above the summary: tap Direct (line
redraws), toggle Trail off (route leaves the SRT), More opens the planner
prefilled; clear route → next trip follows Settings again.

### 2026-08-08 earlier (sixteenth session, omega) — app v1.14.0+48, map-layers v0.14.0 (DEPLOYED)

Bennett's list: darker navy dark theme playing nicer with the greens; drop
the "Auto" base-map pill (the 4-pill row wrapped "Satellite" mid-word);
tools-screen dark-mode contrast (sublink icons were brandDark-on-navy, the
blurb was black54-on-navy); accessibility (high contrast, large UI,
sidewalks too thin — especially on satellite); Google Play R8 warning;
McHan → Legacy Park rides S Church St on `quiet`; Fred Garrett St (GIS
still says Howe St); "The Paperclip" label; formal SRT name; a
prefer-the-trail toggle. `pytest` 68/68, `flutter test` 74/74, analyze
clean, `route_sweep` 300 trips → 3% flagged (baseline was 5%).

**Backend (map-layers v0.14.0), deployed + curl-verified:**

1. **The quiet-Church defect**: the bike-lane stress penalty was baked at
   graph build, calibrated for `balanced` (H-lane ×10 → net 4.0). Under
   `quiet` (streets M 8 / MH 20 / H 40) that fixed 3.5 net made Church St's
   lane the CHEAPEST corridor — the tolerance most averse to Church was the
   only one still routed onto it. Now the edge carries `lane_stress` in
   extras (`(danger, lit, lane_stress)`) and `_astar` prices the lane at
   `max(speed-baked, factors[stress] × LANE_STRESS_RELIEF (⅓))` per rider.
   Balanced calibration is unchanged by construction (12 × ⅓ / 0.4 = 10).
   Verified on the live graph: McHan → Legacy Park quiet AND balanced now
   read McHan → Fred Garrett St → University St → Furman College Way → SRT
   (Bennett's "quiet" line); direct takes the 0.2 mi shorter Church St lane
   hop, which is what "direct, traffic and all" means, and warnings still
   disclose it.
2. **`?trail=0`** (`_TRAIL_PREF` contextvar, plan-cache key, response echo):
   SRT factor floors at 1.0 — the trail is neutral, not forbidden.
3. **`STREET_RENAMES`** (`_gis_rename`/`_rename_label`): HOWE ST → Fred
   Garrett St at graph ingest (steps + voice), search labels, and search
   queries are aliased new-name→GIS-name so "fred garrett" finds Howe rows.
4. **`LANDMARKS` + `landmarks` layer** (point, `/map-layers/landmarks.geojson`,
   searchable): first entry The Paperclip (34.8487, -82.3834 — the SRT
   switchback climb between the second Lakehurst St crossing and Traxler St).
5. **SRT formal name**: graph/step/layer-label name is now "Prisma Health
   Swamp Rabbit Trail" (line names — Green/Blue/Orange/Gold — stay per-
   segment props on the srt layer; the graph keeps ONE name so steps merge).
6. Springer CUSTOM_PATHS note corrected: the lot east of the tunnel is the
   **South Ridge** Apartments (was written "Southernside").

**App (v1.14.0+48):**

7. **Darker navy** `_navy*` set (surface 0xFF0B1424 down to 0xFF060C18) and
   the dark scheme's `primary` is now `_leafOnNavy` (0xFFA9CB7F) so the
   brand green reads on navy.
8. **Base picker is Light/Dark/Satellite** — `MapBase.auto` survives as the
   internal fresh-install default (follows the theme); the picker shows auto
   resolved to the theme's base and any tap makes it explicit.
9. **Tools screen**: sublink icons `brandOnSurface(context)`, blurb
   `onSurfaceVariant`.
10. **Accessibility (Settings → Accessibility)**: "High contrast" (stronger
    scheme in both themes + thematic map lines ×1.7 width, min 0.85 opacity,
    re-added live via `_restyleLineLayers`) and "Large text & controls"
    (MaterialApp builder wraps a `TextScaler` ≥1.3, clamped 2.0, on top of
    the device scale). Sidewalks are 3.0 px @ 0.65 for everyone (were
    1.8 @ 0.5); satellite base boosts ALL thematic lines ×1.35 (imagery is
    busy — hairlines vanished into rooftops).
11. **"Prefer the Swamp Rabbit Trail"** switch (Settings → Riding, default
    on, persisted `prefer_trail`) → `trail=0` on `/route` when off.
12. **R8 for the Play warning**: `isMinifyEnabled`/`isShrinkResources` true
    + `proguard-rules.pro` keeping maplibre/geolocator/local-notifications
    (JNI/reflection) and `-dontwarn` Play Core. APK 83.4 MB / AAB 56.4 MB —
    barely smaller (the weight is native .so libraries R8 can't touch); the
    point is Play's optimization checklist, not size.

Device test (v1.14.0+48): dark theme noticeably deeper navy; layers-sheet
base pills fit on one line; Tools sublinks + footer legible in dark; toggle
High contrast → map lines visibly bolden without reopening; Large UI scales
text app-wide; sidewalks readable over satellite; Settings shows Prefer-
the-trail + Accessibility; route McHan → Legacy Park on quiet = Fred
Garrett → Furman College Way → trail (voice says "Fred Garrett Street" and
"Prisma Health Swamp Rabbit Trail"); search "Fred Garrett" and "Paperclip"
both hit; R8 build passes smoke (search, route, nav, layers, report,
BCycle deep link) — minification is the risky bit, test everything once.

### 2026-08-08 earlier (fifteenth session, omega) — app v1.13.0+47, map-layers v0.13.0

Bennett: "routing broken 4 McHan St → Legacy Park, specifically the Springer
tunnel — the real line is Springer → Briar → University Ridge through
Southernside" + "how do I find other broken routing?" + navy dark theme,
light/dark toggle, satellite base. `pytest` 63/63, `flutter test` 74/74.

**The Springer tunnel bug was FOUR stacked defects (map-layers v0.13.0):**

1. **PCC has no Springer St east of the tunnel** (an 8 m stub) and neither
   does the county centerline or OSM — the street exists on the ground but
   in no dataset, so the tunnel dead-ended in the graph. Two fixes:
   (a) **county gap-fill** — TRA_STREETCL segments whose MIDPOINT has no
   PCC coverage join the graph as 'L' floored by posted speed (~3.2k
   segments, interstates/US highways excluded; graph 44k → 48.5k nodes);
   (b) the CUSTOM_PATHS entry is now the REAL alignment: east portal →
   straight along the Springer roadbed → Briar St's south end. The old
   hand-drawn lot line (which crossed Church St mid-air and became a fake
   ramp via a junction connector) is deleted.
2. **Church St's bike lane out-priced everything**: 0.4x flat. Now
   `_lane_factor` = worse of posted-speed (≥45 ×4, ≥40 ×3, ≥35 ×2.5) and
   the PCC stress of the street under the paint (M ×2, MH ×3.75, **H ×10**
   → net 4.0: on the streets that kill, paint barely helps). The stress
   lookup is NAME-MATCHED first (a short lane piece at a junction sits
   nearest the cross-street's rating — Church's lane matched Springer's L).
3. **OSM footways were trail-cheap (0.4)**: new category `footway` (bike
   0.9, walk 0.8, roll 1.0) so apartment breezeways never beat the real
   street beside them; cycleway/path/pedestrian stay `path` 0.4.
4. Verified: McHan → Briar reads **McHan → Howe → Francis → Springer →
   Springer Street (tunnel) → Springer St → Briar St**. Legacy Park &
   Cleveland Park trips ride Howe (= Fred Garrett St) → Furman College Way
   → SRT → Green Line — Church-free; the 5.4 km "uturn onto SRT" step is
   the REAL Green Line hairpin at Woodland Wy, not a bug.

**"How can I tell?" — `python3 scripts/route_sweep.py [N] [modes...]`**:
samples seeded node-pair trips over the live graph and flags >1 U-turn per
8 km, >40% unnamed distance, >300 m on connectors, >2.6x crow-fly, and
fallbacks/errors. Current baseline: 240 trips → 5% flagged, all explainable
(SRT-bias detours + long-trip trail hairpins); walk 100% clean. Run before
and after any weights change (seeded → comparable), exit 1 above 10%.

**App (v1.13.0+47):**

5. **Navy dark theme**: dark basemap is now OpenFreeMap **fiord** (navy)
   instead of `dark` (near-black), and the Material dark scheme's surfaces
   are deep navy (`_navy*` consts in theme.dart) to match.
6. **Base map picker** in the layers sheet: Auto / Light / Dark / Satellite
   (SegmentedButton, persisted `map_base`). Satellite = inline Esri World
   Imagery raster style (`satelliteStyleJson` — keyless; no glyphs needed
   since all app symbols are bitmap icons). `Auto` follows Settings →
   Appearance as before. Style swaps reuse the existing re-add machinery.

Device test (v1.13.0+47): dark mode = navy map + navy chrome (not black);
layers sheet base picker: satellite shows imagery with route/pins intact
after the swap, toggling base mid-route keeps the line; McHan → Briar St
walks the tunnel; night route still prefers lit streets.

### 2026-08-07 earlier (fourteenth session, omega) — app v1.12.0+46, map-layers v0.12.0

Bennett: "we almost never recommend Church St even though it has a bike
lane" + avoid the notoriously dangerous roads (White Horse, S Academy, Pete
Hollis, Augusta's 4-lane stretch…) using the Vulnerable Road Users data —
fatality-weighted, since downtown logs many incidents but few deaths — plus
Duke streetlight data for night routing, and both datasets as default-off
map layers. `python3 -m pytest tests/` 61/61, `flutter test` 74/74.

**Backend (map-layers v0.12.0):**

1. **Crash-danger multiplier on every street + bike-lane edge.** Score per
   stress/lane segment = Σ(fatal 10 / injury-crash 1 / other 0.25) within
   100 ft, per 100 m; edge weight × (1 + min(score, 3) × 0.75) → cap ×3.25.
   Measured: Pete Hollis 3.0, S Academy 2.9, White Horse 2.9, Wade Hampton
   1.8, Augusta 1.5 vs downtown Main St 0.5–0.75 (the fatality weighting is
   what separates them). Edge tuples grew index 8: `(danger, lit)`.
2. **A painted lane is not protection**: bike-lane edges also multiply by
   posted speed (≥45 ×3, ≥40 ×2.25, ≥35 ×1.5), and **SHARROW rows (322) are
   out of the graph entirely** — paint saying "share" is not infrastructure.
   Verified: the Springer-corridor trip that rode the Church St lane in
   v0.11 now takes the tunnel path; 4 McHan St → Cleveland Park now reads
   McHan → Howe → Springer → tunnel → SRT (Bennett's own line).
3. **Night routing** (Duke streetlights, `Ped.lighting`): a street with <1
   pole per 100 m counts unlit; after dark (ET month table; `?night=0/1`
   override, response echoes `night`) unlit street edges pay ×1.6 walk/roll,
   ×1.35 bike. Request-scoped contextvar — no signature threading; plan
   cache key includes the flag.
4. **New layers**: `vulnerable-crashes` (1,650 pts, killed/injured props) and
   `street-lights` (~40k pts).
5. **`Ped.crashes_vulnerable` had NO geometry index** — the danger join
   seq-scanned it 29k times (graph build 24 s → 469 s). `IX_crashes_
   vulnerable_geometry` created on prod; `indices: geometry` added to
   projects/pedestrian-deaths.yaml. Build back to ~24 s.

**App (v1.12.0+46):**

6. **Crash history heatmap** (`isHeatmap` LayerDef): severity-weighted
   (killed 1.0 / injured 0.35 / other 0.15), yellow→deep red, default off.
7. **Street lights** (`isCircle` LayerDef): tiny amber dots, minZoom 12,
   default off, `enableInteraction: false` (40k dots must not eat taps).

Device test (v1.12.0+46): layers sheet shows "Crash history (bike/ped)" +
"Street lights" toggles (off); heatmap glows red along White Horse/Academy;
bike route near Church St corridor uses the Springer tunnel; night trip
prefers lit streets (server clock — test after sunset or curl `?night=1`).

### 2026-08-07 earlier (thirteenth session, omega) — app v1.11.0+45, map-layers v0.10.0

Bennett's ride feedback round 2: jarring reroute audio, missing shortcuts
(Springer tunnel class), stronger SRT pull, speed limits vs the stress layer,
and "E Washington St" spoken as the letter E. `flutter analyze` clean,
`flutter test` 74/74, `python3 -m pytest tests/` 53/53 (pytest is the test
runner now; new Python tests are pytest-style plain functions).

Post-release fixes on the same day (backend only, all deployed + verified):
map-layers v0.11.0 moved the OSM ETL onto the `MapLayers.osm_paths` pipe
(see item 1); v0.11.1 fixed Nominatim search labels showing a bare house
number ("4" / "McHan Street, Downtown" → "4 McHan Street"); v0.11.2 fixed
Mc-name casing everywhere — search arms now use the DB's SMART_CAPITALIZE
(was INITCAP → "Mchan St") and `_titleize` mirrors it in Python for
turn-by-turn step names.

**Backend (map-layers v0.10.0):**

1. **OSM paths in the routing graph** (`_osm_path_rows`): Overpass ways —
   cycleway/path/pedestrian, footway minus `footway=sidewalk|crossing`, and
   street tunnels (`highway=residential|service|…` + `tunnel=yes`; the
   Springer St tunnel is `highway=residential` in OSM, NOT a path) — within
   SEARCH_BOUNDS. ~4.9k ways. True paths route as category `path` (0.4, car-
   free), street tunnels as `L`. OSM ways named "Swamp Rabbit*" are skipped —
   the trail is already its own cheaper category. Graph: 37.7k → **44.4k
   nodes**, build ~16 s → ~20 s. CUSTOM_PATHS stays for anything OSM doesn't
   know.
   **v0.11.0 moved the ETL onto a pipe** (Bennett: auditable + tunable):
   `Pipe('plugin:map-layers', 'osm_paths', 'greenville')` →
   `MapLayers.osm_paths` (way_id PK, name, highway, street flag, LINESTRING
   4326 + GiST), registered by `projects/osm-paths.yaml`, synced daily by
   the **`osm-paths` job** in `mrsm-api-bwg-1` (first sync verified: 4,873
   rows). The graph build reads the pipe first; the direct Overpass fetch +
   `<output>/osm-paths.json` disk cache (7-day TTL) remains only as the
   fallback for environments where the pipe has never synced.
2. **Speed limits corroborate PCC stress** (`_stress_floor`): each stress
   segment picks up the posted SPEED of the nearest `county.TRA_STREETCL`
   centerline (nearest to the segment MIDPOINT so side streets don't inherit
   the arterial they dead-end into), then the stress level is floored:
   ≥45 → H, ≥40 → MH, ≥35 → M. Escalation only. ~3.3k of 28.4k segments
   escalate (Pete Hollis Blvd's 40 mph blocks → MH; any 45 mph road rated L
   was a data gap). Escalated levels also drive the no-bike-lane warnings.
3. **SRT bias deepened again**: srt factor quiet 0.2 → **0.12**, balanced
   0.28 → **0.18**, direct 0.4 → **0.3**; walk 0.55 → **0.45**, roll 0.5 →
   **0.45**. A balanced bike now detours up to ~5.5× the direct distance to
   ride the trail. Verified: 101 N Main → Swamp Rabbit Cafe rides the SRT.
4. **PROPOSED bike lanes no longer route as real** — the bike-lanes graph
   source now filters `STATUS != 'PROPOSED'` (133 rows of paint that doesn't
   exist yet were priced at 0.4 like real lanes). The *visual* bike-lanes
   layer still draws them — decide separately if that should change.

**App (v1.11.0+45):**

5. **Reroute audio gentler**: the ToneGenerator beep is GONE (MainActivity's
   `bwg/tone` channel deleted — even TONE_PROP_ACK read as an alarm on the
   road); the announce phrase is now "Finding a new route." instead of
   "Rerouting." Backoff/quiet-notice governor unchanged.
6. **TTS pronunciation** (`spokenText()` in nav.dart, unit-tested): cardinal
   prefixes (E/N/S/W/NE/NW/SE/SW → East/…), street suffixes (St/Rd/Ave/Blvd/
   Dr/Ln/Ct/Hwy/Pkwy/… → Street/…), units (ft/mi → feet/miles). "St" before a
   capitalized name means Saint ("St Francis Dr") unless that word is itself
   a suffix ("E North St Ext" → Street Extension). Display text stays
   abbreviated — only `_speak` calls in `_announce` are expanded.

Device test checklist (v1.11.0+45):
- Voice says "East Washington Street", "Pete Hollis Boulevard", "In 400
  feet…" — never the letter E or "S T".
- Go off-route: NO beep; a calm "Finding a new route." once; then the usual
  quiet backoff.
- Springer St → University Ridge: still rides the tunnel path.
- Bike routes lean visibly harder onto the SRT; Pete Hollis/45 mph roads
  avoided unless direct is chosen (and warned when ridden).
- Publix-class shortcuts (paths/tunnels the county GIS lacks) now route.

### 2026-08-07 earlier (twelfth session, omega) — app v1.10.1+44 (BUILT + SIGNED)

**GPS navigation never worked on Android — root cause found, one line of XML.**
`flutter analyze` clean, `flutter test` 69/69, signed APK + AAB, versionCode 44.

`android/app/src/main/AndroidManifest.xml` had removed geolocator's service
since the very first app commit (`c08570a`):

```xml
<service android:name="com.baseflow.geolocator.GeolocatorLocationService"
         tools:node="remove"/>
```

That service is what serves the position **stream**. `GeolocatorPlugin` binds
it at startup; `StreamHandlerImpl.onListen` then does:

```java
if (foregroundLocationService == null) {
  Log.e(TAG, "Location background service has not started correctly");
  return;   // no events.error, no endOfStream
}
```

No exception, no Dart-visible error, no `onDone` — `getPositionStream` simply
emits nothing, forever. `getCurrentPosition()` runs through
`MethodCallHandlerImpl` and is unaffected, which is why Start always found the
rider, `_locateMe` worked, and the blue dot worked, while the follow camera,
step advancement, off-route detection and reroutes were dead from the first
second of every trip.

**This is why c0463dc did not fix it.** That session read "frozen map" as a
fix-RATE problem and set `AndroidSettings(intervalDuration: 1s)` plus a
watchdog — correct changes to a stream that was never alive. The watchdog then
resubscribed every 5 s forever and converted a silent hang into a recurring
"Waiting for GPS…" toast, which read as flaky GPS. Both of those changes are
KEPT; they are right, they were just downstream of the real fault.

Fix: restore the `<service>`, with `tools:remove="android:foregroundServiceType"`
so it stays a plain **bound** service. `startForeground()` is reachable only
from `enableBackgroundMode()`, which fires only when `AndroidSettings` carries
a `foregroundNotificationConfig` — map_screen.dart passes none. So the
`FOREGROUND_SERVICE` / `FOREGROUND_SERVICE_LOCATION` permission removals STAY,
and there is still no Play justification to write. Verified in the merged
manifest (`build/app/intermediates/merged_manifests/release/.../AndroidManifest.xml`):
service present, `exported="false"`, no `foregroundServiceType`, and zero
`FOREGROUND_SERVICE*` permissions in the APK.

Regression guard: `test/android_manifest_test.dart` fails if the service is
ever node-removed again, or if a foreground permission reappears without the
config to back it.

Device test (v1.10.1+44) — this is the one that matters, omega has no phone:
- Start a trip and stand still: the puck must keep updating and the trip bar
  ETA must tick. Before this fix, nothing moved and "Waiting for GPS…" toasted
  every ~5 s.
- Walk/ride a block: steps advance, voice announces, off-route + reroute work.

### 2026-08-07 earlier (twelfth session, omega) — app v1.10.0+43 (BUILT + SIGNED)

Bennett: "the bike lane warning, Report, Clear route, layers/GPS are crowding
the viewport — zooming in/out feels claustrophobic", plus a compass bug. App
only; **no backend change**, so `map-layers` stays at v0.9.1 in prod and
nothing was redeployed. `flutter analyze` clean, `flutter test` 67/67, signed
APK (87 MB) + AAB (58 MB), signer SHA-1 `537F9A88…A843`, versionCode 43 —
**not device-verified** (no phone on omega).

1. **Hazards button replaces the warning banner.** `_hazards(route)` in
   map_screen.dart builds one list — fallback note, `visibleWarnings()`, the
   hill summary. When it is non-empty, the control rail grows a badged amber
   ⚠ button opening `_openHazardsSheet()`: the same lines, the "dashed red on
   the map" note, the elevation graph, and a "See every turn" row into the
   steps sheet. `_warningBanner` is **deleted**; the elevation card is out of
   `_routePreview` too. The steps sheet no longer repeats the route-wide
   caveats (per-step red subtitles stay).
2. **Warning threshold 200 ft → 1000 ft**, as `warnMinFt` in nav.dart, now the
   default arg of `visibleWarnings([minFt])`. `AppState._warnFt` /
   `warnFt` / `setWarnFt` / the `warn_ft` pref are **deleted** — the slider
   went away in v1.9.1, so the persisted value was dead weight that would have
   pinned old installs at 200. Almost every Greenville trip crosses a short
   unmarked block; at 200 ft the banner cried wolf on routes that were fine.
3. **Control rail replaces the FAB stack.** One rounded `Material` column of
   `IconButton`s (compass / layers / hazards / locate) instead of 2–3 separate
   round FABs — narrower, reads as one object, leaves the map's middle for
   pinch-zoom. Buttons with nothing to say are absent.
4. **"Clear route" FAB deleted** — the ✕ on the route summary bar directly
   below already calls `_clearRoute`. Two cancels for one route was half the
   crowding. "Report" stays an extended FAB (only shown when no route is
   drawn, i.e. the least crowded state); its icon is now
   `add_location_alt_outlined` so it never reads as the hazards triangle.
5. **Compass fix**: the native MapLibre compass drew itself *under the status
   bar* and vanished on tap (it fades out facing north). `compassEnabled:
   false` now; ours is a rail button that appears only when `_bearing.abs() >
   2`, rotates its needle to stay pointing north, and taps to
   `CameraUpdate.bearingTo(0)`. `_onCameraMove` tracks `_bearing` and repaints
   only on a >2° change, and never mid-navigation (the camera rotates on every
   GPS fix there and the compass is hidden anyway).

Device test checklist (v1.10.0+43):
- Plan a route with a real bike-lane gap: NO banner on the map; the rail
  shows a ⚠ with a count; tapping it lists the gaps + hills + elevation graph.
- A route whose only gap is a block or two: no ⚠ button at all (1000 ft bar).
- Rotate the map two-finger: compass appears in the rail *below* the status
  bar, needle points north; tap → map swings north-up and the button leaves.
- Idle map: rail + Report only. Route drawn: rail + alternatives + route bar
  (✕ clears it). Mid-nav: rail is just locate (+ ⚠ if the route has any).

### 2026-08-07 later (eleventh session, omega) — app v1.9.1+42, map-layers v0.9.1 (DEPLOYED)

Bennett's dark-theme + polish feedback on v1.9.0. `flutter analyze` clean,
`flutter test` 66/66, Python 39/39; signed APK + AAB versionCode 42.

1. **Dark theme sweep**: mode pills, alternatives/"Different route" chips,
   planning chip, elevation preview card (+ line/label colors via
   `brandOnSurface`/painter param), warning banner (`warnBg`/`warnFg`/
   `warnAccent` helpers in theme.dart — amber-on-dark-brown in dark mode),
   nav trip bar (surface + onSurfaceVariant, End keeps white-on-red),
   upcoming strip, steps sheet (done-steps use `disabledColor`; black26/45
   were invisible on dark), bcycle/road-info secondary text. Pattern: map
   overlays use `Theme.of(context).colorScheme.surface`, never Colors.white.
2. **Settings**: "I use a wheelchair" (dropped " (roll)", also in the
   directions sheet); "Smallest gap worth a warning" slider REMOVED (AppState
   `warnFt` logic + persistence kept at default 200 ft — UI only).
3. **Puck reads in isometric**: `iconPitchAlignment: 'viewport'` (billboard —
   at 60° tilt a map-pitched icon foreshortened to a sliver; this was "too
   flat"), and renderPuck redrawn 2.5D: radial-gradient sphere, grounded
   blurred ellipse shadow, glyph drop shadow, 48 dp. TRUE 3D model would need
   a custom native MapLibre render layer — deliberately not done.
4. **Search finds businesses**: Nominatim was fallback-only, so ANY local hit
   (street prefix) hid every POI. Now `/map-layers/search` = curated
   BIKE_BUSINESSES matches + local (parking/stops/addresses/streets) +
   Nominatim POIs filling remaining slots (q ≥ 3 chars), deduped by label,
   with a server-side 1.1 s min-interval guard on Nominatim (usage policy).
   Beyond OSM coverage would mean a paid geocoder (Google Places) — not done.
5. **"Clear route" extended FAB** whenever a route is drawn and not
   navigating: clears line/pin/endpoints, camera back to flat (tilt 0).
6. **SRT geometry verified current — nothing was stale on the backend**: the
   `srt` layer + routing graph read `SRT.segments_owners` (projects/srt.yaml,
   sourced from the BWG Google My Map via plugin:gmaps). Gold Line (1.72 mi,
   TR) is in the DB, in the served layer, and routable (curl-verified route
   rides it end-to-end). If a segment ever goes missing on-device again:
   check the pregenerated `srt.geojson` in the container's output dir and
   the 24 h graph cache (restart the container to rebuild), and re-sync
   `mrsm compose sync pipes --file projects/srt.yaml` if the My Map changed.

Device test (v1.9.1+42): dark mode end-to-end (preview + warnings + nav bars
+ steps sheet legible); puck upright and readable at nav tilt; search "Willy
Taco" / "Swamp Rabbit Cafe" returns results; Clear route FAB flattens map;
wheelchair labels; no warn slider in Settings.

### 2026-08-07 (tenth session, omega) — app v1.9.0+41 (BUILT + SIGNED), map-layers v0.9.0 (DEPLOYED + curl-verified)

Bennett's feedback from a real ride (Springer St tunnel → South Ridge lot →
University Ridge → Cleveland St). All implemented; `flutter analyze` clean,
`flutter test` 66/66, `python3 tests/test_route_graph.py` 39/39; signed APK
(84 MB) + AAB (56 MB) built, signer SHA-1 `537F9A88…A843`, versionCode 41 —
**not device-verified** (no phone on omega, as ever).

1. **WRONG-TURN FIX (backend)**: `_build_steps` measured maneuver bearings on
   a single geometry segment, so a ~3 m junction jog right before a left turn
   announced "Bear right" (the Anderson St → Dunbar St report). Bearings now
   measured over ~15 m (`STEP_BEARING_LOOKAHEAD_M`, `_bearing_into` /
   `_bearing_out_of`). Regression test reproduces the exact jog: old code says
   right, new says left.
2. **CUSTOM_PATHS (backend)**: curated off-grid connectors — new category
   `path` (car-free; priced like a bike lane, neutral for roll, own-surface so
   no `no_sidewalk`/`no_bike_lane` warnings). First entry: **Springer St
   tunnel** (OSM way 338586347) + South Ridge Apartments lot up to University
   Ridge. Served at `/map-layers/custom-paths.geojson` ("Shortcuts & tunnels"
   layer in the app, bike+walk modes). Verified live: route Springer St →
   University Ridge steps read "Bear left onto Springer St tunnel path";
   route-stats shows `path: 4 edges / 0.2 km`. Add more entries to the
   CUSTOM_PATHS list in map-layers.py and redeploy.
3. **Reroute governor (app)**: `RerouteGovernor` in nav.dart (unit-tested).
   Off-route rider heading BACK toward the route is left alone; repeat
   reroutes in one spell back off 15→30→60→120 s; only the first reroute of a
   spell beeps + speaks ("Rerouting."), the third says once "Looks like you
   know a shortcut. I'll keep the route updated quietly", everything else is
   silent. Spell resets after 30 consecutive on-route fixes (NOT a short blip
   — a fresh reroute passing through the rider must not reset the backoff).
   Tone softened: `TONE_PROP_ACK` at volume 70 (was BEEP2 @ 85).
4. **Nav visibility (app)**: maneuver card icon 42→56, distance 26→34 w800,
   instruction 16→20 w600 (2 lines), "then" row white70 @15, warning lines
   12.5→14, upcoming strip 40 px tall w600 @14, trip bar ETA 20→24.
5. **Route options row (app)**: the current plan now renders as the FIRST chip
   (filled green, icon + label + duration) ahead of the alternatives — the row
   reads as a set of selectable itineraries, so picking bike+bus over pure
   bike is an explicit visible choice. Tapping the current chip opens steps.
6. **Dark mode (app)**: Settings → Appearance (Match device / Light / Dark,
   persisted `theme_mode`, default system). Dark Material theme + OpenFreeMap
   `dark` basemap. A style swap wipes MapLibre sources/layers/images:
   `build()` detects the URL change, clears `_styleReady`/`_addedLayers`/
   `_puckImages`, and `_onStyleLoaded` re-adds everything and re-pushes the
   active route + puck (`_activeStyle` field). Test dark toggle WITH a route
   drawn and mid-nav.
7. **TTS voice (app)**: `_initTts` picks a Google en-US "network" voice when
   the engine offers one (falls back silently).

Device test checklist (v1.9.0+41):
- Ride past a turn where the route jogs: instruction must match the real
  turn direction (Anderson → Dunbar was the failing case).
- Go off-route and STAY off: one soft tone + "Rerouting", then silence except
  a single "shortcut" notice; back on route for ~30 s then off again →
  announced again.
- Springer St tunnel: route 4 McHan St-ish → County Square area with bike;
  route should use the tunnel path; "Shortcuts & tunnels" layer draws it.
- Settings → Dark: map + app go dark; with a route drawn, the line survives
  the swap; toggle back mid-nav and the puck/route re-appear.
- Route preview: green filled chip = current plan with duration; alternatives
  beside it.
- Nav card legible at arm's length in sunlight.

### Hotfix 2026-08-06 — app v1.8.1+40: nav froze on a real ride (GPS fix rate)

Bennett rode with turn-by-turn and it "hangs on the first turn" — the follow
camera never tracked him. TWO causes, one of them still latent in v1.8.0:

1. **His phone was running v1.6.1+37.** Every nav fix since (distanceFilter
   0, camera glide, arrow puck…) was built on omega and never sideloaded —
   omega has no adb. Lesson: a "fixed" that never crossed the USB cable isn't
   fixed.
2. **geolocator's Android default fix interval is FIVE seconds.** The app
   passed a generic `LocationSettings`, which sends no `timeInterval`, and
   `geolocator_android` `LocationOptions.java` falls back to `5000` ms. One
   fix per 5 s (FusedLocation can defer further) reads as a frozen map on a
   bike — even v1.7's `distanceFilter: 0` build would have felt broken. Fix:
   explicit `AndroidSettings(accuracy: bestForNavigation, distanceFilter: 0,
   intervalDuration: 1 s)` in `_subscribeNavPositions()` (map_screen.dart).
3. Defense in depth, since the position stream can also die silently
   (platform error, provider stall, unnoticed onDone): every fix stamps
   `_lastFixAt`; a 5 s `_navWatchdog` timer resubscribes whenever fixes go
   quiet for >10 s and toasts "Waiting for GPS…" once per dry spell; the
   stream's `onError` no longer tears anything down. Watchdog cancelled in
   `_stopNav`/dispose.

Device test (v1.8.1+40): sideload FIRST (`adb install -r`), ride a short
route — camera must glide with you continuously (~1 Hz), maneuver card must
advance through turns, and pulling the phone's location off/on mid-ride must
recover within ~15 s with the GPS toast.

### 2026-08-06 late (ninth session, omega) — app v1.8.0+39 (BUILT + SIGNED), map-layers v0.8.0 (DEPLOYED + curl-verified)

Bennett's follow-up feedback on the multi-modal work, all implemented, tested
(`flutter analyze` clean, `flutter test` 58/58, Python tests 34/34), built
(signed APK + AAB, versionCode 39, SRA key `537F9A88…A843`) — but, as ever,
**not device-verified** (no phone on omega).

1. **Alternate routes** (the new feature). Backend `?alt=N` (1–3, `plan`
   pinned to bike/walk/roll): penalty-method k-shortest — each pass re-runs
   A* with the previous passes' edges costing `ALT_AVOID_FACTOR` (1.5×);
   virtual terminus edges are never penalized (would just degrade the snap).
   `alt_distinct: false` discloses "no genuinely different way" instead of
   inventing a detour. Composite transit/BCycle plans ignore `alt` (shape is
   fixed by stops/docks). Verified live on the 101 N Main → Rutherford Rd
   trip: alt 0/1/2 = three distinct geometries (3.5 / 4.14 / 3.22 mi), sane
   durations; one-block trip honestly returns `alt_distinct: false`. Cost:
   N+1 plain A* passes (~40 ms each) — nothing for the VPS. App: "Different
   route" chip in the alternatives row (only for plain plans), cycles alt
   1→2→3→base; toast when the alternate came back identical; "· alternate N"
   marker in the preview subtitle + steps-sheet header. Reroute during nav
   deliberately returns to the base route (old avoid-pairs are meaningless
   from a new position).
2. **Thematic line layers subdued** so the route line owns the screen
   (Bennett: "far too many colors"): bus-routes opacity 0.45/width 2.2
   (faintest — per-route colors made the worst tangle), bike-lanes 0.55,
   bike-stress 0.6, srt 0.65 (sidewalks already 0.5). Route line unchanged
   (white casing 8 + color 5 reads fine over the faded context).
3. **E-bike labelling**: preview banner icon/word and steps-sheet header now
   say "E-bike" (`Icons.electric_bike`) when the plan was priced for one
   (`NavRoute.planDisplayLabel` / `planIcon` — server keeps plan key + label
   plain `bike` on purpose; alternatives chips relabel via
   `AppState.useEbike`).
4. **Steps sheet colors steps by mode**: maneuver icon tinted with the leg's
   `routeLegColors` color (bike blue / walk teal / bus purple / BCycle red)
   via `NavRoute.stepModes()` (walks the board/alight + rent/dock maneuvers;
   composite `access_mode` profiles like `ebike:direct` normalize via
   `footMode`). Warn-red icon dropped — missing infrastructure still
   disclosed by the red subtitle line + dashed map spans.

### Superseded 2026-08-06 (eighth session) — app v1.7.0+38, map-layers v0.7.0

Bennett is demoing to executive director **Jasmine Vanadore in a few days** —
this session's goal is production-ready navigation. Work was paused on the
laptop (which has the phone + adb) and continues on omega (which does NOT have
adb — Bennett will device-test with Claude later). All code below is
committed on `main`, `flutter analyze` clean, `flutter test` **48/48 green**,
Python `tests/test_route_graph.py` 31/31 green — but **nothing is built or
device-verified yet**.

#### Backend map-layers v0.7.0 — DEPLOYED to prod and verified via curl

1. **parking-landuse**: new `roadway` kind (from `Parking.dtmp_pavement`, 701
   features, listed first so lots/garages draw on top). Colors now match
   Bennett's spec + the parking Grafana dashboard: roadway `#66BB6A` (green),
   lot `#F57C00` (orange), garage `#FBC02D` (yellow). Verified live: kinds =
   `{roadway: 701, lot: 823, garage: 33}`.
2. **Multi-modal default** (`_route_multimodal`): with ≥2 modes selected and
   no pinned plan, the transit-inclusive itinerary is chosen unless it's more
   than `MULTIMODAL_MAX_SLOWDOWN` (1.6×) slower than the fastest single-mode
   plan — picking bike + bus means "use them together", the pure-bike plan
   stays one alternatives-chip tap away. Verified live: `modes=bike,transit`
   on the test trip now returns `bike-transit` (30.0 min) with `bike`
   (22.5 min) as the alternative; previously `bike` won outright.

#### App v1.7.0+38 — code complete, NOT built, NOT device-tested

1. **Nav follow camera responsive** (`map_screen.dart`): position stream
   `distanceFilter` 5 → **0** (with 5 m, a stopped rider got NO fixes — the
   follow camera AND off-route rerouting both froze exactly when someone
   pulled over); camera throttle 800 → 600 ms; animation 600 → **1100 ms**
   (slightly longer than the ~1 s between fixes ⇒ one continuous glide
   instead of hop-per-fix). Course-up bearing (GPS heading while moving, else
   the route segment's own bearing) already rotated the next turn to the top
   of the screen from the moment Start is tapped — unchanged.
2. **Arrow puck** (`map_icons.dart` `renderPuck`, `map_screen.dart`): new
   `puck` source + `lyr-puck` symbol layer (topmost), rotated to the nav
   bearing, updated every fix, snapped onto the route line when <30 m off it.
   Two styles via Settings (`AppState.puckStyle`): Google-blue arrow
   (default) or the travel mode's icon (cyclist / walker / wheelchair) in a
   green disc with a heading wedge — this IS the "stylized person" feature
   Bennett asked about, done as a rotated bitmap. (True animated 3D models
   would need a custom MapLibre render layer; not worth it.) The native blue
   dot hides while navigating (`myLocationEnabled: _locationEnabled &&
   !_navigating`).
3. **Reroute tone**: `MethodChannel('bwg/tone')` → `MainActivity.kt` plays
   `ToneGenerator.TONE_PROP_BEEP2` (no plugin, no asset) before the
   "Rerouting." TTS + toast. Reroute latency itself is fixed by the
   `distanceFilter: 0` change above (3 consecutive fixes >45 m ≈ 3–4 s now;
   previously unbounded). Backend load: one extra `/route` call per reroute —
   nothing.
4. **Hills on the route line** (`nav.dart`): computed CLIENT-side from
   `elevation_profile` (`hillRanges` / `hillCollection` / `hillSummary`), so
   no server graph changes and the server's carefully noise-gated ADA `steep`
   disclosure for wheelchairs is untouched. Severity: `mod` ≥4% grade,
   `steep` ≥7% **or** ≥5% with ≥60 ft rise (a long 6% hill is a real hill —
   this is what catches N Main's sustained 6.3%/76 ft climb, verified against
   the live profile: its max segment is 7.7%, so an 8% gate would show
   NOTHING on Bennett's own test route), `vsteep` ≥10%. Noise gates mirror
   the server (≥8 ft rise over ≥25 m). Drawn as `route-hills` layer over the
   route line, amber → deep orange → red (`hillColors` in theme.dart); banner
   + steps sheet get a summary line ("About 0.3 mi … up to ~8% grade —
   expect a hard climb. Steep stretches are shaded orange–red on the map.").
   E-bike wording softened ("your e-bike's motor will help"); `roll` returns
   null (server ADA warning already covers it). Known gap (pre-existing):
   composite transit/BCycle plans have no `elevation_profile`, so no hill
   shading there.
5. **Warning threshold** (`AppState.warnFt`, default 200 ft, Settings
   slider 0–1000): `NavRoute.visibleWarnings(minFt)` drops
   no_bike_lane/no_sidewalk warnings shorter than the threshold ("About 0 ft
   of this route…" is dead). `steep` warnings are never filtered. Dashed-red
   map spans still draw regardless — only the banner text is gated.
6. **Per-mode route colors** (`NavRoute.routeCollection`): the route source
   is now a FeatureCollection split at board/alight (transit) and rent/dock
   (BCycle) maneuvers; each leg carries a `color` property (`routeLegColors`
   in theme.dart: bike `#1565C0`, walk/roll `#00897B`, transit `#7B1FA2`,
   bcycle `#E2231A`), bus legs use the official Greenlink `route_color`.
   Single-mode routes = one feature in the mode's color. The old
   "props['color'] = routeColor" hack in `_planTrip` is gone.
7. **Search clears stale routes**: `_selectResult` calls `_clearRoute()`
   when a route is drawn, so picking a new search result no longer leaves the
   old blue line pointing nowhere.
8. **Settings screen** (`lib/screens/settings_screen.dart`, NEW; linked from
   the top of the Tools/hamburger screen): stress tolerance, e-bike, roll,
   BCycle, warning threshold slider, puck style radio.
9. **Layers**: label "Parking garages" (dropped " (live)"); sidewalks
   lightened `#1565C0` → `#7BAFDE` at 0.5 opacity (new `LayerDef.opacity`
   field) so the route line stands apart; parking-landuse `matchColors`
   updated to the three-kind palette above; **minZoom removed** from
   `bcycle`, `repair-stations` and `parking-garages` — the chosen mechanism
   for "pins disappear when zoomed out": sparse layers (≤ a few dozen
   features) simply stay visible at every zoom and MapLibre's symbol
   decluttering handles overlap; dense layers (bus-stops, bike-parking) keep
   their minZoom.

#### DONE 2026-08-06 evening (ninth session, omega)

1. **Build**: signed release APK (87.3 MB) + AAB (58.4 MB) built on omega,
   both verified — signer SHA-1 `537F9A88…A843` (SRA upload key),
   versionCode 38 / versionName 1.7.0:
   - `app-native/build/app/outputs/flutter-apk/app-release.apk`
   - `app-native/build/app/outputs/bundle/release/app-release.aab`
   Pre-build verification: `flutter analyze` clean, `flutter test` 48/48,
   `python3 tests/test_route_graph.py` 31/31. NOTE for other hosts:
   `android/gradle.properties` pins `org.gradle.java.home=/home/bmeares/sdk/jdk21`
   (omega's path; laptop = `/usr/lib/jvm/java-21-temurin-jdk`, don't commit).
2. **VPS repo checkout pulled** to `99b4991`; prod re-verified live via curl:
   multimodal returns `bike-transit` (default) + `bike` alternative,
   parking-landuse kinds `{roadway: 701, lot: 823, garage: 33}`,
   parking-garages 10 features with same-day occupancy.
3. **Garages `_refreshLive()`/`_ensureLayer()` race triaged — harmless, not
   fixed on purpose.** `addSource(data: url)` points MapLibre at the live URL,
   so the layer self-loads even if the first `setGeoJsonSource` races and
   throws (swallowed); later refreshes land after add completes. The real
   rendering bug was `minZoom: 12`, already removed. If garages STILL don't
   render on device, the race is exonerated — look elsewhere.

#### NOT yet done

1. **Device testing** (needs the phone — Bennett will drive): checklist
   below. Sideload: `adb install -r app-release.apk` (release-over-release
   upgrades in place since versionCode rose to 39).
2. Play upload when Bennett says go (see item 6 of the older list below).

#### Device test checklist (v1.8.0+39; the v1.7 items below still apply)

- Route with Bike selected → "Different route" chip next to the alternatives
  chips → tap: new line drawn, subtitle says "· alternate route 1"; tap
  through 2, 3, then back to the base. On a one-block trip: toast "No
  genuinely different route found".
- E-bike toggled on → preview bottom-left says "E-bike" with the electric
  bike icon (not "Bike"); steps-sheet header too; the Bike alternatives chip
  relabels.
- Bike + Bus route → steps sheet: bike-leg maneuver icons blue, bus steps
  purple, walk steps teal.
- Bus routes / bike lanes / SRT / bike stress layers on + a multi-modal
  route drawn → route line clearly dominant, layers read as faded context.

#### Device test checklist (v1.7.0+38)

- Route **101 N Main → Other Lands (731 Rutherford Rd)**, Walk and Bike:
  hill shading on the N Main climbs, hill line in the banner + steps sheet,
  elevation graph unchanged.
- Search a place, route to it, then search somewhere else → old route line
  fully gone, green dot on the new place.
- Bike + Bus selected → "Go here" → bike-transit itinerary by default,
  bike/bus legs in different colors, pure-bike in the alternatives chips.
- Start nav: view rotates to the route bearing immediately (next turn at
  top), blue arrow puck (not the dot), camera glides ~continuously, ongoing
  notification, pan away → Re-center chip.
- Go off-route (~3 fixes >45 m): double-beep, "Rerouting.", route redraws
  from current position, same itinerary.
- Settings: switch puck to mode icon → nav shows cyclist/walker disc;
  threshold slider up → small no-bike-lane warnings disappear from preview.
- Layers sheet: "Parking garages" (no "(live)") renders pins + occupancy
  sheet; Parking land use = green roadway / orange lots / yellow garages;
  BCycle + repair stations visible when zoomed way out; sidewalks noticeably
  fainter than the route line.
- Regression: reports pin + submit, BCycle sheet/deep link, feature taps.

### Hotfix 2026-08-03 — app v1.6.1+37

On-device report: after "Get directions" the whole bottom overlay (route
preview, Start, layers/report FABs) vanished; the FABs flashed back while the
"Finding route…" chip was up. Root cause: `ElevationProfile`'s root was a
stretch-aligned Row, and the preview hosts it in a shrink-wrapping Column
whose children get UNBOUNDED height → `BoxConstraints forces an infinite
height` → the layout exception took the entire overlay down whenever a route
had ≥30 ft of climb (i.e. almost always). The chip branch doesn't render the
preview, which is why the FABs reappeared during planning. Fix: explicit
`SizedBox(height:)` around the Row; regression test
`test/elevation_profile_test.dart` pumps the widget inside an
unbounded-height Column (fails on the old code with the exact exception).
Lesson for this codebase: anything added to the bottom-overlay Column must
tolerate unbounded height — no stretch/spaceBetween/Expanded at its root
without an explicit height.

### Shipped 2026-08-02 (seventh session) — map-layers v0.6.0, app v1.6.0+36

Nav overhaul + new layers + submissions. Design doc:
`docs/superpowers/specs/2026-08-02-nav-layers-ux-design.md`. Reviewed by Codex
(8 findings; 7 fixed — route-shadowing of the garages endpoint, NaN 500s,
notification cancel race, stale-route race on rapid pill taps, gesture
detection rework to distance-based, submit-point rate limit + 8 MB photo cap +
coordinate validation; the 8th is the composite-plan elevation gap below).

1. **Navigation usable**: follow camera throttled to ≤1/800 ms with 600 ms
   animations (was: a queued 900 ms animation per GPS fix — the jitter). User
   pan detaches the camera (detected by DISTANCE from the rider, >120 m, not
   by timing — follow animations overlap any time window); a **Re-center**
   chip re-attaches. Ongoing silent notification (`flutter_local_notifications`,
   `NavNotifier`, ops serialized) shows next turn + distance + ETA; updated on
   step change or 15 s; POST_NOTIFICATIONS added; gradle got core-library
   desugaring. Full background/foreground-service nav deliberately NOT done
   (manifest still strips FOREGROUND_SERVICE_LOCATION for Play review).
2. **SRT bias**: srt factors bike 0.28 balanced / 0.2 quiet / 0.4 direct,
   walk 0.55, roll 0.5. `balanced` no longer byte-reproduces v0.5.0 routes
   (traffic weights unchanged, trail discount deeper). Tests pin the
   trail-over-parallel-street choice both ways (bike takes it, walk doesn't).
3. **Pill cycle**: tapping a selected mode pill cycles Bike→E-bike→off /
   Walk→Roll→off (AppState.cyclePill; SegmentedButton `emptySelectionAllowed`
   is how "tapped the last selected pill" is detected — the empty set is never
   applied). Last mode never deselects; variants reset on the way out.
4. **New layers**: `bike-businesses` (curated list in map-layers.py, default
   ON), `parking-garages` live occupancy (per Bennett: reads the cached
   `Parking.garages_counts_map` pipe, NOT nwave directly; default off),
   `parking-landuse` fill polygons lots-vs-garages (default off; first fill
   layer in the app — `LayerDef.isFill` + matchProp/matchColors),
   merged `sidewalks` layer (county ∪ city-not-near-county, 80 ft dedupe)
   replacing the two sidewalk toggles in the app. Bus routes icon now
   `Icons.route` (was identical to stops).
5. **Search recents** (8, MRU, shared_preferences) shown on focus; focusing a
   field with text re-runs the search — clearing a route no longer strands the
   user. "Finding your route…" chip while planning (was: dead air).
6. **Point submissions**: `/map-layers/submit-point` → `MapLayers.point_submissions`
   + AddPointSheet ("Add a missing place here" in the map actions sheet).
   NO usernames — shelved until Jasmine's feedback. OSM upstream = manual.
7. **BCycle launch fix**: manifest `<queries>` (scheme `bcycle` + package
   `com.bcycle`) — Android 11+ package visibility was why it always fell to
   the browser; launches try `externalNonBrowserApplication` first.
8. **Elevation preview**: `elevation_profile` from the router (plain plans),
   sparkline in the preview (steep >8% stretches red), per-step "↑ N ft" +
   steep-climb callouts in the steps sheet/maneuver card (client-side, from
   per-step `climb_ft`, mirroring the server's 8 ft noise gate).

Known limitations (seventh session): composite transit/BCycle plans carry
`climb_ft` but no `elevation_profile` (the app just omits the graph); the
in-process rate limit resets on container restart and is per-worker;
**everything above is untested on hardware** — same warning as before.

### Shipped 2026-08-01 (sixth session) — map-layers v0.5.0, app v1.5.0+35

Bike sub-options and terrain. Design doc:
`docs/superpowers/specs/2026-08-01-bike-suboptions-design.md`.

1. **Acceptable stress level** — `?stress=quiet|balanced|direct`, three presets
   that re-weight the bike penalties. A tolerance never removes an edge, so a
   route always exists and the over-tolerance stretches stay disclosed. It also
   sets what earns a "no bike lane" warning (quiet flags ML and up, direct only
   MH/H). **`balanced` reproduces the historical stress weights exactly**, and
   a test pins that. Note the scope of that claim: traffic costing is
   unchanged, but hills (below) are priced for everyone, so responses do
   differ from v0.4.1 even with no new parameters.
2. **E-bike** — `?ebike=1`. 6.7 m/s (15 mph) instead of 4.2, and a quarter of
   the hill cost. No separate stress table: whether traffic is tolerable is the
   rider's call and they have a control for it.
3. **Hills, for every human-powered mode.** Elevation comes from
   `county."TOP_CONTOUR"` (4 ft interval, SRID 6570, already a pipe in
   `projects/county.yaml` — no new ETL). Sampled per graph NODE, not per edge,
   because `_astar` already holds both endpoints of the edge it is relaxing —
   so the edge tuple never changed and nothing downstream was re-indexed.
   Climb costs `CLIMB_FACTOR` metres of flat per metre of rise (bike 8, e-bike
   2, walk 4, roll 20) and adds `CLIMB_SEC_PER_M` to the ETA. Descents are free
   but never bonused. Walk/roll legs steeper than ADA's 1:12 come back as
   `warn: 'steep'`.
4. **App**: `AppState.useEbike` + `AppState.stress` (persisted), an e-bike
   switch and a Quiet/Balanced/Direct segmented control in the directions
   sheet (shown only when a bike is selected), "E-bike" labelling on the
   cyclist mode, and "↑ 210 ft" in the route preview once climb reaches 50 ft.

Graph build 6.0 s → **16.0 s** (the contour lookup), still cached 24 h and
warmed in a background thread at API start. 100% of the 37,739 nodes got an
elevation; range 572–3180 ft.

**Units — checked, because this repo has been bitten before.** The routing
graph is 4326 throughout and every length is metres (`_equirect_m`); the feet
CRSes never reach the router. `ELEVATION` is FEET, verified against landmarks
(Falls Park 928 vs ~940 actual, downtown 976 vs ~966, Travelers Rest 1056 vs
~1070) — if it were metres downtown would read 294. `_climb_m()` converts to
metres before it meets any constant, and `climb_ft` converts back on the way
out. `_srid_units_per_m()` **measures** a CRS's units instead of assuming, so
the contour search radius is right whatever SRID the layer arrives in (3361
and 6570 both measure ≈3.27 units/m — feet, including projection scale).

Sanity of the resulting terrain: edge grades come out median 2.23%, p90 6.1%,
max 23.8%, with 4.1% of edges above ADA's 8.3%. That is a believable Piedmont
distribution.

Blast radius of the terrain change, over the same 180-route sweep the previous
session used: **68% of default routes are byte-identical**, the other 32% are
rerouted around hills; mean distance change −0.07 mi; nothing became
unroutable. ETAs rise across the board (the reported McHan → Other Lands bike
trip goes 25.4 → 36.5 min on 312 ft of climb), which is the intended
correction, not a regression — the old numbers assumed Greenville was flat.

**The `steep` disclosure is noise-gated.** Nearest-contour sampling quantizes
each node to ±2 ft, so two nodes on flat ground can differ by a whole 4 ft
interval — over a 10 m leg that reads as a 12% grade. A leg is only announced
as steep when it rises more than TWO contour intervals (`STEEP_MIN_RISE_FT`
8 ft, outside the error bound) over at least `STEEP_MIN_RUN_M` (25 m), and
never on a synthetic connector. Effect: edge-level steep flags 5.0% → 4.1%;
28% of wheelchair routes mention a grade, averaging 82 m of it. Do not loosen
these without ground-truthing — a wheelchair user planning around a hill that
isn't there is a worse failure than silence.

**Known limitation:** the same quantization means gross climb (and therefore
ETA) is slightly over-counted on long routes — noise accumulates as phantom
rise. Interpolating between the two nearest distinct contours would roughly
halve it at the cost of a second KNN per node (~+10 s on the build). Worth
doing if the ETAs read long on the ground. Deliberately NOT done blind: it
trades bias for variance and deserves a real-world check first.

**Reviewed by two independent readers** (Codex and Fable). Between them: five
defects in the first cut, all fixed with failing-first tests (street fallback
dropped hills; steep descents undisclosed; the app announced `steep` as "no
bike lane"; step folding dropped climb; transit/BCycle omitted `climb_ft`),
plus the two residuals above. Both cleared A* admissibility with the added
climb term, virtual-node elevation, composite mode-key handling, feet/metres
conversions, and the omitted-parameter defaults.

### Shipped 2026-08-01 (fifth session) — map-layers v0.4.1 (backend only)

**Junction connectors land ON a street, not on the nearest street *node*.**
Reported symptom: biking 4 McHan St → Other Lands turned right onto Jones Ave,
rode 37 m south, then U-turned back north onto Cleveland St.

Root cause: graph nodes only exist where `_subdivide` ended a ~120 m chunk, so
the "connect every trail/lane-only node to the street grid" pass attached the
Cleveland St bike lane's south end to a Jones Ave node 48 m SOUTH instead of
the Pearl/Jones/Cleveland junction 12 m away. (The old pass also excluded
*mixed* nodes — where a path already meets a street — from its target set,
which is exactly what made it reach past that junction.) A route arriving at
the junction had no edge onto the lane and had to ride south and double back.

Fix, in `_build_route_graph`:
1. Each trail/lane-only node now snaps to the nearest point **on** a street
   chunk, splitting that chunk there — the build-time twin of what
   `_snap_terminus` already does per request for route termini.
2. `_add_connector` refuses a connector between nodes a real edge already
   joins. Those weigh 1.0× length, so the straight line undercut the road it
   duplicated and the router cut the corner.

Measured over 180 synthetic routes (60 trips × bike/walk/roll), before → after:
U-turn maneuvers **111 → 37**, metres travelled on synthetic connectors
**24,550 → 7,943**, total distance −0.42%, zero change in what's routable.
Graph build 5.7 s → 6.0 s; nodes 37,291 → 37,739. The reported trip now reads
Pearl Av → Cleveland St with no U-turn in bike, walk *or* roll mode, and a
separate mislabeled "Make a U-turn onto N Main St" on the same trip is gone.

Tests: `tests/test_route_graph.py` (new, first Python tests in the repo) —
runs the graph builder + A* + step builder against synthetic source rows with
no database, reproducing the exact reported geometry. `python3
tests/test_route_graph.py`. Verified non-vacuous: all 4 fail on the old code.

Known residual: ~37/180 routes still contain a U-turn. Most are the mirror of
the fixed case — a path node only exists at a chunk endpoint, so leaving the
trail can mean riding past your exit and coming back. Splitting *trail*
geometry from the street side would fix it but would fabricate SRT access
points that don't exist on the ground; don't do it without checking real
access points.

### Shipped 2026-08-01 (fourth session) — v1.4.0+34, map-layers v0.4.0

1. **Routing termini snap to the nearest point ON an edge, not the nearest
   node** (`_snap_terminus` / `_snap_edge` / `_edge_index` in
   `plugins/map-layers.py`). Mid-edge termini become per-request *virtual
   nodes* (the edge is split at the projection; partial edges overlay the
   shared graph via `_astar(extra_adj=, extra_nodes=)` — never mutated). This
   fixes the McHan St → Haynie St U-turn: the old node snap walked ~45 m WEST
   to the nearest chunk endpoint and doubled back east past the origin. Both
   termini on the same edge get a direct bridge (`_bridge_same_edge`) or the
   path would detour to an endpoint and back. Verified against a synthetic
   graph reproducing the exact geometry.
2. **Routing graph warms at API start** (background thread in `init_app`:
   graph + nearest/edge indexes + transit data), so the first route request
   doesn't pay the ~6 s build.
3. **Bottom UI clears the system navigation bar.** Everything bottom-anchored
   (FABs, place card, route preview, nav trip bar) now lives in ONE
   `Positioned` overlay offset by `MediaQuery.padding.bottom` — 3-button-nav
   phones (~48 dp inset) no longer draw under the buttons. Attribution (i)
   margin lifted too; snackbars are `SnackBarBehavior.floating` via the theme.
4. **Route preview moved from the top pile to the bottom** (summary + Start
   closest to the thumb; warnings and alternative chips stack above it). Top
   chrome is now just search + mode segments (+ pick banner).
5. **Layers load lazily.** Sources are only fetched when a layer first turns
   visible (`_ensureLayer`, `_addedLayers`); line layers insert
   `belowLayerId: 'lyr-highlight-line'`, pins below `'lyr-route-casing'`, so
   draw order is preserved. Cold start no longer downloads the multi-MB
   county/city sidewalks GeoJSON when nobody is in walk mode.

### Shipped 2026-08-01 (third session) — v1.3.0+33

1. **Multi-modal routing.** `/map-layers/route` takes `?modes=bike,walk,transit` (plus `roll=1`, `bcycle=1`, `plan=<key>`); every itinerary the selection allows is costed and the fastest returned, the rest in `properties.alternatives[]`. Plan keys: `bike`, `walk`, `roll`, `bcycle`, `bike-transit`, `walk-transit`, `roll-transit`. Transit access is by bike when bike is selected (5 km catchment, "load your bike on the front rack" at boarding). Legacy `?mode=` still works. Full parameter/behaviour notes in `DATA.md`.
2. **Sidewalk-aware walking + wheelchair mode.** Graph edges now carry `has_sidewalk`, computed at build time by an indexed `ST_DWithin` (80 ft) against `county.sidewalks` + `city."Sidewalks"` (created `IX_city_Sidewalks_geometry` — the city table had no spatial index and the join was a nested loop without it). `roll` weights penalize a sidewalk-less street 8×, so a wheelchair route takes the longer sidewalked way. Graph build went 4.8 s → ~5.7 s.
3. **The route tells you what it's missing.** `properties.warn_ranges[]` / `warnings[]` / per-step `warn` mark every stretch with no sidewalk (walk/roll) or no bike lane (medium-plus stress, no facility). The app draws those spans **dashed red over the route line**, banners the sentence in the preview, flags them in the steps sheet and in the maneuver card mid-turn.
4. **Street fallback, disclosed.** If the mode's network can't reach, `_route()` retries with flat street weights + a 2.5 km snap and sets `fallback: 'street'` + `fallback_note`; the warnings stay written for the *requested* mode.
5. **BCycle bike share** (`plugins/bcycle.py`, new): live GBFS availability at `/bcycle/stations.geojson`, `bcycle` map layer with dock pins, a feature sheet showing bikes/e-bikes/open docks, and an "Open the BCycle app" handoff (per-station deep link → `bcycle://` → Play Store). Also a routing plan: walk → unlock → ride → dock → walk, with `rent`/`dock` maneuvers. `BCycle.stations` pipe (`projects/bcycle.yaml`) is the outage fallback for pin locations only.
6. **Trip planner with a start point** (`lib/screens/directions_sheet.dart`): From (your location by default) / To, each searchable or pickable by tapping the map, swap button, mode multi-select chips, and the roll / BCycle sub-toggles. Reachable from the search bar's ⌗ directions button, the map-tap actions sheet, and the ⚙ on a searched place card.
7. **Nav view reads like Google Maps.** Thematic overlays come off while navigating so the basemap street grid and labels are legible; arrowheads land on the map at every upcoming turn (rotated to the heading, dropping off as they're passed); an always-on horizontal strip lists the turns still ahead; alternatives chips re-route in one tap; rerouting keeps the same itinerary instead of silently switching modes.
8. **Modes are a set, persisted.** `AppState.modes` is a `Set<TravelMode>` in shared_preferences with `roll` / `useBcycle`; layer visibility is the union across selected modes; the top segmented control is `multiSelectionEnabled`.
9. `DATA.md` corrected: **SRID 3361 is feet, not metres** (`+units=ft`) — the old note would have made every distance literal 3.28× off.

### Shipped earlier (first session on omega)

1. **No more forwarding language.** Reports are stored and shown on the map; only a staff notification email goes out (to `data@…`, never to an office). Owner resolution is still stored per report. Banner in `report_sheet.dart`, post-submit toast, `RoadInfoSheet` note and the `tools_screen.dart` blurb all reworded.
2. **Reports layer is prominent**: `defaultOn: true`, always drawn (`iconAllowOverlap`), larger pin, and `_refreshReports()` re-fetches the layer right after a submit — the server now writes the row synchronously so the new pin appears immediately.
3. **Layer polish**: SRT → "Prisma Health Swamp Rabbit Trail" + `Icons.cruelty_free`; "Bike stress" (dropped " (PCC)").
4. **Dot soup killed**: point layers render Material-icon teardrop pins (`lib/map_icons.dart` paints them to PNG at the device pixel ratio → `addImage` → `addSymbolLayer` with zoom-interpolated `iconSize`). Per-layer `minZoom` (bus stops/bike parking 13, repair 11); the layers sheet shows "Zoom in to see these" when a layer is below its zoom.
5. **Selection feedback**: `HapticFeedback.selectionClick()` on every feature tap + a `highlight` GeoJSON source (wide translucent line / ring circle, added between the line and pin layers) cleared when the sheet closes.
6. **WOTR tools entry** expands into three sublinks: search dashboard, Felt map, and the `bikewalkgreenville.org/roads` story.
7. **Turn-by-turn navigation** (new): see below.

### Shipped 2026-07-30 (second session) — v1.2.0+32

1. **Walk + transit turn-by-turn** (`plugins/map-layers.py` v0.2.0): `?mode=bike|walk|transit` on `/map-layers/route`. Shared graph, per-mode `MODE_FACTORS`/speeds; transit = walk→board Greenlink shape→alight→walk with `board`/`ride`/`alight` steps (flat 8-min wait — no stop_times yet). "Low-stress" wording dropped everywhere.
2. **App is mode-aware end to end**: route request uses the selected mode; preview banner shows mode icon + "Bike route / Walking route / Transit route" (transit shows Greenlink route + boarding stop, banner + line in official route color via `coalesce` on a per-feature `color`); switching the mode segment re-routes a drawn route.
3. **Nav view**: follow camera now zoom 17.5 / tilt 60 (isometric); route preview tips to 35° after the bounds fit; maneuver card is tappable → full upcoming-turns sheet; preview banner has a list button too.
4. **Directions everywhere**: feature sheets (bus stops, bike parking, repair stations…) have a green Directions button routing to the feature's own coordinate; WOTR shrank to an icon.
5. **Tap = long-press now**: plain map taps open the same actions sheet (directions / report / who-owns) — long-press was undiscoverable. First tap with keyboard up just dismisses it.
6. **SRT dot-soup fixed**: highlight circle layer got `['==', ['geometry-type'], 'Point']` (it was drawing a ring on every vertex of tapped lines); line highlight filtered to LineString.
7. **Search UX**: results select into a big green bottom place card (replaces the tiny SnackBar) with a mode-verb button ("Bike/Walk/Bus here"); clear (X) button in the search field; `resizeToAvoidBottomInset: false` fixes the white band after backgrounding with the keyboard open.
8. **Tools screen**: WOTR sublink descriptions removed.

### Turn-by-turn navigation

- Backend (`plugins/map-layers.py`): routing edges now carry street names; `/map-layers/route` returns `properties.steps[]` (maneuver, instruction, name, distance, `start_index`, location, bearing) plus `distance_mi`. Legs merge into one step unless the street name changes or the bearing swings past `STEP_TURN_MIN_DEG` (22°, or 60° when staying on the same street); sub-25 m steps fold away, and a hair-length depart leg is promoted into the first real step.
- App: `lib/nav.dart` (`NavRoute`, `RouteStep`, `NavProgress` — snap-to-polyline, current step, distance to maneuver, off-route distance, imperial formatting; covered by `test/nav_test.dart`), driven from `map_screen.dart`:
  - Route preview banner → **Start** enters navigation.
  - Course-up follow camera (zoom 17, tilt 45, GPS heading while moving), maneuver card with "then …" preview, bottom trip bar (ETA + distance left, Steps sheet, End), voice mute toggle.
  - Voice prompts via `flutter_tts` at ~230 m and again at ~45 m; screen kept awake with `wakelock_plus`.
  - Off-route: 3 consecutive fixes >45 m → "Rerouting" + silent re-route from the current position. Arrival at <25 m remaining.
- New deps: `flutter_tts`, `wakelock_plus`.

## NOT yet done / next up

1. **On-device testing of everything above.** No Android device has been attached to omega for any of these sessions, so pins, haptics, highlight, voice guidance, the follow camera, the turn arrows, the dashed warning spans and the trip planner are all untested on hardware. Sideload the APK and ride/walk a route before promoting. `adb uninstall org.bikewalkgreenville.app` first if a debug build is installed.
2. **Judgement call on the sidewalk data.** The `no_sidewalk` warnings are only as good as `county.sidewalks` + `city."Sidewalks"`: 57% of stress segments have no sidewalk within 80 ft, which is plausible for greater Greenville but includes genuinely unmapped sidewalks outside the city. The wording ("no sidewalk **mapped**") is deliberately hedged. If it reads as too alarmist on the ground, raise `SIDEWALK_NEAR_FT` or soften `WARNING_LABELS` rather than dropping the disclosure.
3. **Bike-share plan rarely wins.** With 13 downtown docks, walking to one usually costs more than it saves, so `bcycle` mostly shows up as a slower alternative. That's honest; don't tune `BIKESHARE_*` to force it.
4. **Transit wait is still a flat 8 min** (`TRANSIT_WAIT_MIN`) — `stop_times` isn't ingested, so transit durations are estimates and there is no departure time anywhere in the UI.
5. **Jasmine's call on forwarding** — the app no longer promises forwarding anywhere. If BWG decides to forward after all, the owner contact info is still stored per report.
6. **Play upload** when ready: the AAB above; `gplay release --package org.bikewalkgreenville.app --bundle build/app/outputs/bundle/release/app-release.aab --track internal --wait`; listing assets pattern in trail-counter `app-native/play/`.
7. Optional: moderation/rate limiting on the public POST endpoints (still none — see DATA.md "Gaps"); a `bcycle` daily job in prod if the station-location fallback should stay fresh (`docker exec mrsm-api-bwg-1 mrsm sync pipes -c plugin:bcycle -s daily --name bcycle -d -y`).

## Secrets (present on omega, all gitignored, chmod 600)

- `~/projects/bikewalkgreenville/.env` — `FELT_API_TOKEN`, `MRSM_SMTP_*`, **`MRSM_WALK_AUDIT_NOTIFY_RECIPIENT`** (renamed from `…_TEST_RECIPIENT`), `MRSM_SQL_BWG`.
- `~/projects/bikewalkgreenville/app-native/keystore.properties` + `sra-upload.keystore` — shared SRA upload key, alias `sra-upload`, SHA-1 `537F9A88AAB6623CFA91F0FBABCE6F95E705A843`.

If lost: keystore + passwords in Google Drive "Backup" (search `sra-upload`); SMTP creds now also in the prod container at `/meerschaum/config/plugins.json`; otherwise ask Bennett.

## Toolchain on omega

Flutter `~/flutter/3.38.7`, Android SDK `~/Android/Sdk` (build-tools 36), **JDK 21 at `/usr/lib/jvm/java-21-temurin-jdk`** — `app-native/android/gradle.properties` pins `org.gradle.java.home` there (system JDK is 25, which maplibre_gl won't build with). Change that line if the repo moves hosts again.

## Prod deploy procedure

Plugins live in the docker volume `mrsm_bwg_api_root`, not a bind mount:

```bash
scp -P 2269 plugins/<plugin>.py meerschaum@mrsm.io:/tmp/
ssh -p 2269 meerschaum@mrsm.io \
  'docker cp /tmp/<plugin>.py mrsm-api-bwg-1:/meerschaum/plugins/ && docker restart mrsm-api-bwg-1'
```

Jobs run inside the same container: `docker exec mrsm-api-bwg-1 mrsm show jobs`, create with e.g.
`docker exec mrsm-api-bwg-1 mrsm sync pipes -t transit -s daily --name transit -d -y`.

The VPS repo checkout (`meerschaum@mrsm.io:~/projects/bikewalkgreenville`, ssh port 2269) still needs a `git pull`.

## Hard-won findings (do not rediscover)

- **maplibre_gl 0.26.2 fork quirks**:
  - Feature taps do NOT fire `onMapClick`; layers added with `enableInteraction` (default true) route taps to `controller.onFeatureTapped`. `map_screen.dart` `_onFeatureTap` queries props via `queryRenderedFeaturesInRect` (logical px first, device-px fallback). Route/pin/highlight overlays use `enableInteraction: false` so they don't swallow taps.
  - Layer draw order = insertion order: lines → highlight → symbol pins → route → pin.
  - `addImage` bitmaps are drawn in physical pixels — render them at `MediaQuery.devicePixelRatio` or they come out tiny on 3x screens.
  - Needs **JDK 21** (`sourceCompatibility 21`); Flutter's configured JDK is 17 → pinned in `android/gradle.properties`.
- Material icon glyphs rendered by code point survive `flutter build`'s icon tree-shaking only because the `IconData`s are `const` in `theme.dart`/`nav.dart`. Keep them const.
- **Release builds strip all logs** — debug with `flutter run --debug` when a callback silently doesn't fire.
- Debug↔release reinstall needs `adb uninstall` (signature mismatch); release↔release upgrades in place ONLY if pubspec `+N` increases.
- Basemap: OpenFreeMap Liberty (`https://tiles.openfreemap.org/styles/liberty`), keyless. GeoJSON endpoints are consumed directly as MapLibre sources — no vector-tile infra.
- Local API dev loop: `mrsm compose start api --port 8899` + `adb reverse tcp:8899 tcp:8899`; the app's Dio client pins prod-then-localhost:8899.
- `mrsm` CLI: `-f` = `--force`, use `--file`; `up --dry` registers pipes; `mrsm sql bwg "<query>"` executes raw SQL (writes too, `-y`).
- Greenlink GTFS feed: `https://gtfs.greenlink.cadavl.com/GTA/GTFS/GTFS_GTA.zip` (service dates through 2027-07).
- OSM Overpass (`overpass-api.de`) 504s intermittently — the bike-parking job retries daily, so a failed run is not a code problem.
- Device UI driving (Pixel 8 Pro on Bennett's laptop): screen 1008x2244; screenshots read at 898x2000 → multiply by 1.12. `adb shell input swipe X Y X Y 900` = long-press. Grant location: `adb shell pm grant org.bikewalkgreenville.app android.permission.ACCESS_FINE_LOCATION`.

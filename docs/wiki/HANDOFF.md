# Handoff — work continues on host `omega` (repo at `~/projects/bikewalkgreenville`)

Updated 2026-08-07 (tenth session). Read this + `DATA.md` before touching
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

Flutter `~/flutter-sdk`, Android SDK `~/Android/Sdk` (build-tools 36), **JDK 21 at `~/sdk/jdk21`** — `app-native/android/gradle.properties` pins `org.gradle.java.home=/home/bmeares/sdk/jdk21` (system JDK is 25, which maplibre_gl won't build with). Change that line if the repo moves hosts again.

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

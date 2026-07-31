# Handoff — work continues on host `omega` (repo at `~/projects/bikewalkgreenville`)

Updated 2026-07-30 (second session on omega). Read this + `DATA.md` before touching anything.

## ⚠️ Repo is PUBLIC on GitHub

`gh repo view bmeares/bikewalkgreenville` → PUBLIC. **Never commit secrets** — no SMTP passwords, no keystores, no connection strings. `.gitignore` covers `.env`, `keystore.properties`, `*.keystore`, `*.jks`. Plugin config in `mrsm-compose.yaml` interpolates from the environment (`$MRSM_SMTP_*`); real values live only in the untracked `.env` and in the prod container's config.

## State (all TESTED; backend DEPLOYED)

- **`app-native/`** — native Flutter app (map-first, MapLibre GL), v**1.2.0+32**, applicationId `org.bikewalkgreenville.app`. `flutter analyze` clean, `flutter test` 10/10 green, signed release APK + AAB built on omega (signer SHA-1 `537F9A88…A843`, the shared SRA upload key).
  - `build/app/outputs/flutter-apk/app-release.apk` (86 MB)
  - `build/app/outputs/bundle/release/app-release.aab` (58 MB)
- **Backend live on bwg.mrsm.io**: `plugins/walk-audit.py`, `plugins/map-layers.py` (now serving turn-by-turn `steps`), `plugins/bike-parking.py`, `plugins/gtfs.py`.
- **Prod jobs registered** inside `mrsm-api-bwg-1`: `transit` (daily GTFS sync) and `bike-parking` (daily Overpass sync), alongside `annex-watch`, `trails-output`, `duke`, `who-owns-the-roads`, `parking`. Both verified running with successful first syncs (`mrsm show logs <job>`).
- **walk-audit config** moved off env vars onto Meerschaum config (`plugins:walk-audit:{smtp,notify}`). Prod values live in the container volume at `/meerschaum/config/plugins.json` (chmod 600); the `/meerschaum/.env` hack has been **deleted**.
- `WalkAudit.reports` is empty (the deploy-check row was removed).

### Shipped this session

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

1. **On-device testing of everything above.** No Android device was attached to omega this session, so pins, haptics, highlight, voice guidance and the follow camera are untested on hardware. Sideload the APK and ride/walk a route before promoting. `adb uninstall org.bikewalkgreenville.app` first if a debug build is installed.
2. **Jasmine's call on forwarding** — the app no longer promises forwarding anywhere. If BWG decides to forward after all, the owner contact info is still stored per report.
3. **Play upload** when ready: the AAB above; `gplay release --package org.bikewalkgreenville.app --bundle build/app/outputs/bundle/release/app-release.aab --track internal --wait`; listing assets pattern in trail-counter `app-native/play/`.
4. Optional: `stop_times` ingest for headways; moderation/rate limiting on the public POST endpoints (still none — see DATA.md "Gaps").

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

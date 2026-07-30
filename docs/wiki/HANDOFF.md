# Handoff — continue on host `omega` (repo at `~/projects/bikewalkgreenville`)

Written 2026-07-30 at end of session on Bennett's laptop. Read this + `DATA.md` before touching anything.

## ⚠️ Repo is PUBLIC on GitHub

`gh repo view bmeares/bikewalkgreenville` → PUBLIC. **Never commit secrets** — no SMTP passwords, no keystores, no connection strings. `.gitignore` already covers `.env`, `keystore.properties`, `*.keystore`, `*.jks`. Bennett asked to move SMTP config from env vars into the Meerschaum compose project file — because the repo is public, the compose file must reference host config (e.g. `MRSM{plugins:walk-audit:smtp}` style) or interpolate, with real values living only in the prod stack file / local host config. Do NOT put the app password literal in any committed YAML.

## State as of this commit (all TESTED and DEPLOYED)

- **`app-native/`** — native Flutter rewrite (replaces Flet `bwg_app/`, which is retired reference). Map-first MapLibre GL app, signed APK verified on Bennett's Pixel 8 Pro. v1.0.0+30, applicationId `org.bikewalkgreenville.app`. All features e2e-tested on device: mode switch (Bike/Walk/Transit), all layers render, search, low-stress routing from GPS, walk-audit report submit (landed in prod DB), feature tap sheets, WOTR contact card, tools screen.
- **Backend live on bwg.mrsm.io**: `plugins/walk-audit.py` (submit/reports.geojson/categories), `plugins/gtfs.py` (Greenlink ingest), `/map-layers/road-info` (nearest-road contact KNN), `/bike-parking/repair-stations.geojson`, GTFS-backed `bus-routes`/`bus-stops` layers (16 routes w/ official colors, 997 stops). `WalkAudit.reports` table is empty (test rows cleaned).
- **Prod deploy method used**: `scp` plugin → VPS `/tmp` → `docker cp` into `mrsm-api-bwg-1:/meerschaum/plugins/` → `docker restart mrsm-api-bwg-1`. Plugins live in docker volume `mrsm_bwg_api_root`, NOT a repo bind mount. VPS repo checkout (`meerschaum@mrsm.io:~/projects/bikewalkgreenville`, ssh port 2269) is at old commit `4af090e` — needs `git pull` after this push.
- **BWG Meerschaum jobs run INSIDE the api container** (`mrsm-api-bwg-1`), alongside `who-owns-the-roads` etc. New projects (`transit.yaml`, walk-audit) are NOT yet registered as prod jobs — that's pending work (below).
- `sql:bwg` direct connection + prod port mapping: Bennett FIXED both (host connects fine now, no tunnel needed). The SSH-tunnel workaround in memory/wiki is obsolete.

## Secrets — ALREADY PLACED ON OMEGA (2026-07-30)

Copied via scp at handoff, all gitignored there, chmod 600:
- `~/projects/bikewalkgreenville/.env` — `FELT_API_TOKEN`, `MRSM_SMTP_*` (data@bikewalkgreenville.org app password), `MRSM_WALK_AUDIT_TEST_RECIPIENT`, **`MRSM_SQL_BWG`** (full prod DB URI — compose works without host-config setup).
- `~/projects/bikewalkgreenville/app-native/keystore.properties` + `sra-upload.keystore` — shared SRA upload key (same as trail-counter/anchored, both also cloned on omega), alias `sra-upload`, SHA-1 `537F9A88AAB6623CFA91F0FBABCE6F95E705A843`.

If ever lost: keystore + its passwords in Google Drive folder "Backup" (search `sra-upload`); SMTP password also in prod api container (`ssh -p 2269 meerschaum@mrsm.io docker exec mrsm-api-bwg-1 cat /meerschaum/.env`); otherwise ask Bennett.

Omega must still provide its own toolchain: Flutter 3.38.x, Android SDK 36 + platform-tools, **JDK 21** (update the `org.gradle.java.home` path in `app-native/android/gradle.properties` if omega's JDK 21 lives elsewhere — laptop path was `/usr/lib/jvm/java-21-temurin-jdk`).

## PENDING WORK (Bennett's last instructions, not yet done)

1. **Config rework**: walk-audit plugin currently reads `MRSM_SMTP_*` env vars with `<root>/.env` file fallback (`_env()` in `plugins/walk-audit.py`). Replace with Meerschaum config (`mrsm.get_config('plugins', 'walk-audit', ...)`); set values in a compose project file locally (secret-free — see PUBLIC warning) and in the prod Meerschaum stack file for the api container. Remove the `/meerschaum/.env` hack afterward.
2. **Deploy prod jobs**: register/schedule the new compose projects (`projects/transit.yaml` daily GTFS sync; bike-parking already has repair_stations pipe) as jobs inside the api container, alongside existing ones (`who-owns-the-roads` etc.). Inspect how existing jobs are defined in the container first (`docker exec mrsm-api-bwg-1 mrsm show jobs`).
3. **Feature change — forwarding removed (pending Jasmine)**: submissions should be visible on the map, NOT forwarded to municipal offices (Bennett will confirm with Jasmine whether forwarding happens at all). Remove ALL user-facing "BWG will forward this to X" mentions: the owner banner in `app-native/lib/screens/report_sheet.dart`, the post-submit toast in `map_screen.dart` (`_openReportSheet`), the note in `tools_screen.dart`, and reconsider the email send in `walk-audit.py` (keep internal notification to data@ at most; keep owner resolution stored in DB — useful later). Make the `reports` layer (`/walk-audit/reports.geojson`) prominent: `defaultOn: true`, refresh its source after a successful submit so the new pin appears immediately.
4. **Layer polish** (`app-native/lib/theme.dart`):
   - SRT: label **"Prisma Health Swamp Rabbit Trail"**, rabbit icon (`Icons.cruelty_free` is Material's rabbit).
   - Bike stress: label "Bike stress" (drop " (PCC)").
5. **Point rendering — kill the dot soup**: uniform teal/purple circles everywhere overwhelm the map. Use proper icons/pins per layer (bus, parking P, wrench, warning): render Material icons to PNG via Flutter `TextPainter`/canvas → `controller.addImage()` → `addSymbolLayer` with `iconImage`, `iconSize` zoom-interpolated. Add `minzoom` per point layer (bus stops ~13, bike parking ~13, repair ~11) so points appear on zoom-in; consider circle→icon swap at zoom threshold.
6. **Selection feedback**: tapping a feature should (a) fire `HapticFeedback.selectionClick()`, (b) visually highlight the tapped feature (dedicated `highlight` geojson source + wide translucent line layer / ring circle layer, populated from the tapped feature's geometry, cleared when the sheet closes). Today it's unclear whether a tap registered.
7. **Commit/deploy when finished** (Bennett's standing instruction for this batch).

## Hard-won findings (do not rediscover)

- **maplibre_gl 0.26.2 fork quirks**:
  - Feature taps do NOT fire `onMapClick` — layers added with `enableInteraction` (default true) route taps to `controller.onFeatureTapped` callbacks `(point, latLng, id, layerId, annotation)`; `featureTapsTriggersMapClick: false` by default. `map_screen.dart` `_onFeatureTap` then queries props via `queryRenderedFeaturesInRect` (logical px first, device-px fallback). Route/pin overlay layers use `enableInteraction: false` so they don't swallow taps.
  - Needs **JDK 21** (`sourceCompatibility 21`); Flutter's configured JDK is 17 → pinned via `org.gradle.java.home=/usr/lib/jvm/java-21-temurin-jdk` in `app-native/android/gradle.properties` (adjust path for omega's JDK 21).
- **Release builds strip all logs** — debug with `flutter run --debug` when a callback silently doesn't fire.
- `flutter create` scaffold trap: changing applicationId requires moving `MainActivity.kt` to the matching package dir or launch crashes `ClassNotFoundException`.
- Debug↔release reinstall needs `adb uninstall` (signature mismatch); release↔release upgrades in place ONLY if pubspec `+N` increases.
- Basemap: OpenFreeMap Liberty (`https://tiles.openfreemap.org/styles/liberty`), keyless. GeoJSON layer endpoints consumed directly as MapLibre sources — no vector-tile infra needed.
- Local API dev loop: `mrsm compose start api --port 8899` (repo plugins loaded) + `adb reverse tcp:8899 tcp:8899`; app's Dio client (lib/api.dart) pins prod-then-localhost:8899. Local venv needed `mrsm compose install packages python-multipart` once.
- `mrsm` CLI: `-f` = `--force`, use `--file`; `up --dry` registers pipes; `mrsm sql bwg "<query>"` executes raw SQL (writes too, `-y`).
- Greenlink GTFS feed: `https://gtfs.greenlink.cadavl.com/GTA/GTFS/GTFS_GTA.zip` (service dates through 2027-07). `stop_times` not ingested yet (headways future work).
- Device UI driving (Pixel 8 Pro `46061FDJG00187` on Bennett's laptop — omega may differ): screen 1008x2244; screenshots read at 898x2000 → multiply by 1.12. `adb shell input swipe X Y X Y 900` = long-press. Grant location: `adb shell pm grant org.bikewalkgreenville.app android.permission.ACCESS_FINE_LOCATION`.
- Play upload (when ready): `flutter build appbundle --release`; `gplay release --package org.bikewalkgreenville.app --bundle ... --track internal --wait`; listing assets pattern in trail-counter `app-native/play/`.

## Cleanup notes

- Two dead memory refs on the old laptop only (memory dir doesn't transfer hosts) — this file is the source of truth.
- `WalkAudit.reports` cleaned of test rows; repair stations (13) + transit tables (16/997/31) are real synced data, keep.
- Prod stack port mapping + external `sql:bwg` access: FIXED by Bennett post-session; ignore tunnel instructions in git history.

# Bike Walk Greenville

Flutter app for Android, iOS, and web. `bwg_app/` is the retired Flet client;
active development is in this directory. The Python API is `plugins/map-layers.py`.

## Routing

The backend runs custom A* over a PostGIS-fed graph, not pgRouting. Sources include
PCC traffic stress, city/county lanes and streets, the Swamp Rabbit Trail, OSM paths,
tunnels, access tags, gates, signals, and live community paths. Automatic service-drive
and parking-aisle shortcuts are excluded; the curated Springer-to-Briar link remains.
A* tracks incoming direction at each junction and prices reversals and turns.
Traffic preference changes costs; mapped private access and mode prohibitions are
constraints. Synthetic connections cannot invent arterial crossings. Busy junctions
and mid-edge arterial crossings incur costs independently of quiet approach streets.
Signal proximity lowers that cost; it does not certify a crossing as safe.

OSM tunnel nodes remain separate from the street above. Individual fence-outline
vertices are not interpreted as gates: this matters at the Springer tunnel portal.
The hand-mapped Springer continuation remains available where access permits it.
No transit or bike-share access leg falls back to an unverified straight line.
Origin/destination snapping is limited to 80 m and checks restrictions.

Graph refreshes publish immutable snapshots. Requests keep using the prior graph
while a refresh builds. A private disk cache keyed to the exact backend code and
community revision IDs accelerates restarts; route-result cache keys include graph
and community generations. Source refresh uses the existing daily OSM pipe and
24-hour graph lifetime. Community changes trigger a background graph refresh.

## Community map

Tap a location to add a place or correction, draw a local route, or define a no-entry
area. The editor supports numbered vertices, coordinate edits, move/insert/delete,
extending either end, quadratic curves, freehand pen strokes, erasing vertices,
and undo/redo. Curves and freehand strokes export as ordinary editable vertices.
Erasing joins the remaining neighboring vertices; review the geometry before publishing.
Tap a community feature to reopen its geometry and description. Tools → Our community map exposes
public revision history and rollback. New edits publish immediately. Rolling back
an edit restores the prior version; rolling back an original contribution withdraws
it. Earlier revisions remain in history. Stale edit targets return HTTP 409.

The map is a BWG community overlay, not a direct edit to upstream OSM or county GIS.
Only path-shaped `shortcut` and `route-suggestion` contributions enter routing,
subject to the same access and crossing guards. Point access/crossing reports are
visible local knowledge; they do not themselves rewrite source road permissions.
No-entry polygons reject intersecting route edges and first/last-meter links for
all modes, including street fallback. Publication and rollback affect new route
requests immediately, independently of graph rebuilding. Invalid/self-crossing
polygons and category mismatches are rejected.
New community paths enter routing after background rebuilding (about 1–2 minutes in the
release check). Users are anonymous. Rate limiting and geometry bounds apply.
Legacy private point submissions are not republished.

BWG's non-endorsement and personal-risk notices appear before navigation, on route
previews, in settings, and in contribution flows. They do not guarantee safe routes.

## Validate and build

```sh
# Repository root
python3 -m pytest tests/ -q
# This directory
flutter analyze
flutter test
flutter build web --release --base-href /bwg-app/ --no-wasm-dry-run
flutter build appbundle --release
```

Web uses a full-screen HTML pointer shield for modal panels because a Flutter
barrier alone does not stop events reaching the MapLibre platform view. Native
panels remain scrollable bottom sheets. Bitmap scaling follows each platform's
MapLibre image decoder; icons do not scale twice with pin size. Attribution occupies
a reserved footer and only the app GPS control is visible on web. Dismissing search
invalidates pending responses. Satellite imagery uses the SC RFA/USGS tile cache
shared with trail-counter instead of Esri.

## Release

Increment `pubspec.yaml` name and code together; update `ios/testflight/whats_new.txt`.
Android package and iOS bundle: `org.bikewalkgreenville.app`.

Google Play open testing is the `beta` track:

```sh
gplay release --package org.bikewalkgreenville.app \
  --bundle build/app/outputs/bundle/release/app-release.aab \
  --track beta --release-notes @play-release-notes.json --wait
gplay status --package org.bikewalkgreenville.app
```

Use the existing upload keystore; never substitute debug signing for a release.
Credentials remain outside git. The configured Play account must have access to BWG.

Build iOS on `mac` in an isolated release directory, preserving existing local work.
Use a login shell, unlock the existing keychain for signing, and relock afterward:
`flutter build ipa --release --export-options-plist=ios/ExportOptions.plist`.
Upload via `xcrun altool` using the configured App Store Connect key. App ID
`6804114643`; external group `BWG Testers` (`21bc6a0a-11e5-4086-8a62-28d3ee4fabfb`).
Set build-specific testing notes, attach that exact build, and submit for beta review.
An upload is distinct from Apple's approval and external tester availability.

Web/API deployment instructions are in `plugins/bwg-app.py`. Back up the existing
bundle and plugin before replacing them; verify the deployed build and route API.

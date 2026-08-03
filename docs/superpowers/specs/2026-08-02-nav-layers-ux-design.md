# Navigation overhaul, new layers, submissions & UX — design (2026-08-02)

Requested by Bennett (implementation judgment delegated). Backend `plugins/map-layers.py`
v0.5.0 → **v0.6.0**, app `app-native/` v1.5.0+35 → **v1.6.0+36**.

## 1. Navigation mode (currently unusable on device)

Problems: the camera re-animates on every GPS fix (900 ms animation each ~5 m),
fighting both itself and any user pan; there is no way to look around without the
camera snapping back; no recenter affordance; nothing persists off-screen.

Design:
- **Follow state** (`_followNav`): on while navigating until the user pans the map
  (detected via `onCameraMove` arriving outside a window around our own programmatic
  animations); a **Re-center** chip re-enables it. While detached the camera stays put
  but progress/announcements continue.
- **Throttled camera**: at most one `animateCamera` per 800 ms, 500 ms duration,
  course-up (GPS heading while moving, else route bearing), zoom 17.5 / tilt 60.
- **Persistent notification** (`flutter_local_notifications`): an ongoing,
  `onlyAlertOnce` notification showing the next maneuver + distance + ETA, updated on
  step change or ≥15 s. Cancelled on End/arrival. Android 13+ permission requested at
  nav start. This is *screen-on* navigation (wakelock already keeps the screen awake);
  a full background foreground-service nav is out of scope (Play review burden —
  FOREGROUND_SERVICE_LOCATION is deliberately stripped from the manifest).

## 2. SRT routing bias

`srt` category factors drop so the trail wins more often: bike quiet 0.35→0.2,
balanced 0.4→0.28, direct 0.5→0.4; walk 0.7→0.55; roll 0.6→0.5. A* stays admissible
(heuristic already uses `min(factors)`). The "balanced == historical" pin is updated:
balanced now means "historical traffic weights **plus a stronger trail preference**",
and the test pins bike ≡ bike:balanced instead of the old absolute numbers.

## 3. Mode pills: multi-click cycle

The top segmented control keeps multi-select, but tapping an already-selected pill
cycles its variant instead of instantly deselecting:

- Bike: off → **Bike** → **E-bike** → off (variant resets on the way out)
- Walk: off → **Walk** → **Roll** → off
- Bus: off → on → off (no variant)

The pill's icon/label change on the second tap (Bike→E-bike, Walk→Roll), which makes
the cycle self-revealing. The last remaining mode never deselects (existing invariant);
its third tap just resets the variant. The explicit switches in the directions sheet
remain and stay in sync (same `AppState` flags). Implemented via
`AppState.cyclePill(mode)` driven from the SegmentedButton selection diff.

## 4. New layers

| id | source | kind | default |
|---|---|---|---|
| `parking-garages` | `Parking.garages_counts_map` (cached pipe, latest row per garage; centroids) | point, `live: true` | hidden |
| `parking-landuse` | `Parking.dtmp_parking` (lots) + `Parking.parking_facilities_greenville` (garages) | **fill** (lots vs garages, two colors) | hidden |
| `bike-businesses` | curated list in `map-layers.py` (starts with Swamp Rabbit Cafe & Grocery; extend by editing the list) | point | **visible** |
| `sidewalks` | county lines ∪ city lines >80 ft from any county line (anti-join dedupe) | line | replaces the two old defs |

`sidewalks-city`/`sidewalks-county` endpoints stay for back-compat; the app uses the
merged layer. Fill-layer support is added to the app's `_ensureLayer`. Feature sheet
shows garage occupancy ("123 of 400 spaces open") from the live props.

## 5. Search recents & routing feedback

- Selected search results persist (`shared_preferences`, MRU, max 8, both the main
  search bar and the directions sheet). Focusing the search field with no results
  shows "Recent" entries; focusing with text re-runs the search — so a cleared route
  is two taps to bring back.
- While a route is being computed a progress chip ("Finding route…") shows above the
  bottom card and the place-card button shows a spinner (fixes silent "Bike here").

## 6. Point submissions (missing bike parking etc.)

`POST /map-layers/submit-point` (category, name, comment, lat, lon, photo) →
`MapLayers.point_submissions` pipe + photo dir, same shape as the walk-audit flow.
App: "Add a missing place" in the map actions sheet → `AddPointSheet` with category
chips (Bike parking, Bike repair station, Water fountain, Bike-friendly business,
Other). **No usernames / no login** — shelved until Jasmine weighs in; OSM upstreaming
stays a manual moderation step later.

## 7. BCycle app launch

Root cause: Android 11+ package visibility — without a `<queries>` declaration the
`bcycle://` intent never resolves and the chain falls through to the Play/web URL.
Fix: manifest `<queries>` for the `bcycle` scheme + the `com.bcycle` package, and
launch attempts try `LaunchMode.externalNonBrowserApplication` first (opens the app
for https app-links too), then external, then Play Store.

## 8. Elevation preview

`_route_core` now emits `properties.elevation_profile`: `[distance_m, elevation_ft]`
pairs per leg boundary (downsampled to ≤120 points), human-powered plans only.
The route preview draws a small area sparkline (CustomPainter) with stretches steeper
than 8 % tinted red; the steps sheet shows "↑ N ft" per step and a red "steep climb"
tag when a step's own grade exceeds 8 % over ≥ 8 ft of rise (mirrors the server's
noise gate).

## 9. Icons

Bus routes toggle icon → `Icons.route`; bus stops keep `Icons.directions_bus`
(pin imagery unchanged and familiar).

## Testing

- `tests/test_route_graph.py`: SRT-bias expectations updated; new test that a route
  prefers the trail over a parallel low-stress street; elevation_profile presence.
- Flutter: pill-cycle state tests, recents persistence test, layer-def sanity
  (fill kind, unique ids), elevation widget smoke test.

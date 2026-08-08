# BWG Data Knowledge Base

Inventory of `sql:bwg` (prod TimescaleDB behind `bwg.mrsm.io`), the pipes that fill it, and the HTTP endpoints the mobile app consumes. 210 pipes, 322 tables. Compose sources: `mrsm-compose.yaml` + `projects/*.yaml`.

## CRS regimes (the #1 integration gotcha)

| SRID | What | Where |
|---|---|---|
| 4326 (WGS84) | app-ready | crash points (`SCDPS.*`, `Ped.*`), `SRT.*`, `public.srt`, `replica.*`; `BikeParking` is plain lat/lon floats |
| 6570 (SC ft) | state plane | `Roads.*`, `pcc.*`, `county.*`, `gcgis.*`, `Boundaries.*`, `city.BusRoutes/BusStops` |
| 3361 (SC **ft**) | HARN | most `city.*` (Sidewalks, BicycleInfrastructure, Streets, Trails), `Parking.*` |

Anything served to the app must `ST_Transform(geom, 4326)`. `pcc.stress_levels.geojson` (jsonb) is pre-transformed. `geometry_columns` lies (`srid=0`) for several `Roads`/`Ped` tables — trust the data, not the view.

**3361 is FEET, not metres** (`select proj4text from spatial_ref_sys where srid=3361` → `+units=ft`; `ST_Length` / geography ratio measures 3.28). Both projected regimes in this DB are ft-based, so a distance literal in `ST_DWithin` means the same thing in 6570 and 3361.

## Feature → data map

| App feature | Tables | Rows | Notes |
|---|---|---|---|
| Bike stress | `pcc.stress_levels` | 28,445 | `stress_level` L/ML/M/MH/H (22154/112/3372/1977/830); per-level split tables; statewide `stress_levels_sc` 402k |
| Bike lanes/infra | `city.BicycleInfrastructure` | 919 | `STATUS` EXISTING/PROPOSED, `BIKE_TYPE` BIKELANE 588 / SHARROW 322 / GREENWAY 9; city limits only |
| Trails / SRT | `city.Trails` (542), `city.ApprovedTrails` (72), `gcgis."PRISMA Health Swamp Rabbit Trail"` (305), `SRT.segments*` | | `SRT.segments_owners` carries maintenance contacts per segment |
| Sidewalks | `county.sidewalks` (18,493 lines), `Sidewalks.sidewalks` (5,325 city **polygons**), `Sidewalks.streets_with_sidewalks` (3,103) | | `Sidewalks.county_sidewalks` is EMPTY (dead) |
| Bus/transit | `transit.routes` (16), `transit.stops` (997, w/ routes-served + Point 4326), `transit.route_shapes` (31 LineString 4326 w/ official colors) | | Greenlink GTFS (`plugins/gtfs.py`, `projects/transit.yaml`, feed `gtfs.greenlink.cadavl.com`); supersedes stale `city.BusRoutes`/`BusStops`. Schedules (stop_times) not yet ingested — headways/frequency a future step. Synced daily in prod by the `transit` job inside `mrsm-api-bwg-1`. |
| Bike parking | `BikeParking.parking_locations` | 283 | OSM Overpass `amenity=bicycle_parking`; lat/lon floats |
| Bike share | `BCycle.stations` | 13 | Greenville BCycle; **locations only** (outage fallback) — live availability is read straight from GBFS by `plugins/bcycle.py`, never stored |
| Bike repair stations | `BikeParking.repair_stations` | ~few | OSM `amenity=bicycle_repair_station` (added 2026-07) |
| Roads/ownership (WOTR) | `Roads.roads` | 35,204 | Owner + Email/Phone/Online Form denormalized per segment; `Roads.contact_info` (20) is the municipal contact lookup (Greenville Cares `cares@greenvillesc.gov`, SCDOT MWRO, six cities, GCPRT…) |
| Speed limits | `county.TRA_STREETCL.SPEED` (35,185), `city.Streets.SPEED` (5,745) | | **No lane-count column anywhere**; observed speeds in `replica."annual-speeds"` (66k, OSM-keyed p50/p85/p95) |
| OSM shortcut paths | `MapLayers.osm_paths` | 4,873 | Overpass cycleway/path/pedestrian/footway + street tunnels; way_id PK; daily `osm-paths` job; feeds the routing graph (`projects/osm-paths.yaml`) |
| Collisions | `Ped.crashes_vulnerable` (1,650) + pedalcycle/pedestrian splits; `SCDPS.sql_bwg_collisions_*` map-ready points | | rich attrs: lighting, junction, contributing factor |
| Boundaries | `Boundaries.boundaries` (6 municipalities), `county_council_districts` (12) | | |
| Bonus | `deflock.cameras_greenville` (83 ALPR), `brokenspoke.neighborhood_overall_scores` (23 BNA), `duke.lighting` (63k streetlights), `Events.events_greenville` | | |

## HTTP endpoints (served by Meerschaum Web plugins at bwg.mrsm.io)

### `plugins/bcycle.py`
- `GET /bcycle/stations.geojson` — Greenville BCycle docks with **live** availability, merged from the system's GBFS 1.1 feed (`bcycle_greenville`: `station_information` + `station_status`). 13 stations, all downtown-ish. Props: `name`, `address`, `bikes`, `ebikes`, `docks`, `is_renting`, `availability` (pre-formatted line), `rental_uri` (per-station Android deep link), `short_id` (the number on the kiosk). Cached 45 s in-process, served `Cache-Control: no-store`, and serves stale-over-empty if GBFS blips. Falls back to the `BCycle.stations` pipe (locations only, `projects/bcycle.yaml`) when the feed is unreachable.
- `GET /bcycle/system.json` — system name/url/phone/email plus `app_discovery_uri` (`bcycle://`) and `app_store_uri`, the chain the app walks to hand a rider off to the BCycle app.
- `get_stations()` is also imported by `map-layers.py` for the `bcycle` routing plan.

### `plugins/map-layers.py`
- `GET /map-layers/index.json` — layer registry (bus-routes, bus-stops, bike-lanes, sidewalks-city, sidewalks-county, **sidewalks** (merged), srt, bike-stress, **parking-landuse**, **bike-businesses**)
- `GET /map-layers/{layer}.geojson` — dissolved overview; `?bbox=&zoom=` → per-feature detail (limit 4000). Builder layers (v0.6.0): `sidewalks` = county lines ∪ city lines >80 ft from any county line (the app now shows ONE sidewalks toggle; the per-source layers stay for back-compat); `parking-landuse` = DTMP surface lots (`Parking.dtmp_parking`, FEAT_CODE 121/122) + garage footprints (`Parking.parking_facilities_greenville`), `kind` = lot|garage; `bike-businesses` = the hand-curated `BIKE_BUSINESSES` list in the plugin (edit + redeploy to add one).
- `GET /map-layers/parking-garages.geojson` — downtown garages with latest occupancy from the cached `Parking.garages_counts_map` pipe (`name`, `capacity`, `occupied`, `percent_occupied`, `availability`, `as_of`). 60 s in-process cache, `Cache-Control: no-store`. **Registered before the `{layer}.geojson` route on purpose — Starlette matches in declaration order.**
- `POST /map-layers/submit-point` — user-submitted missing points (multipart `category` ∈ bike-parking|repair-station|water-fountain|bike-business|other, `name`, `comment`, `lat`, `lon`, `photo`) → `MapLayers.point_submissions`. Anonymous by design (login model shelved pending Jasmine); guards: category whitelist, finite/range coords, 10/hour/IP in-process rate limit, 8 MB photo cap. Moderated by hand before any OSM upstreaming.
- `GET /map-layers/route?from=lat,lon&to=lat,lon&modes=bike,walk,transit[&roll=1][&bcycle=1][&plan=<key>][&ebike=1][&stress=quiet|balanced|direct][&alt=1..3][&night=0/1][&trail=0]` — **multi-modal** directions over an in-process A* graph (SRT + bike lanes + PCC stress); `route-stats.json` for graph health. The legacy single `?mode=bike|walk|transit` still works. `trail=0` (v0.14.0) turns off the SRT bias — the trail prices like a plain calm street; the response echoes `trail`.
  - **Alternate routes (v0.8.0).** `alt=N` with `plan` pinned to a plain plan (`bike`/`walk`/`roll`) returns that plan's Nth alternate: each pass re-runs A* with the previous passes' edges costing `ALT_AVOID_FACTOR` (1.5×), so a genuinely different street wins when one exists. Response carries `alt` and `alt_distinct`; `alt_distinct: false` means no different way exists (the same route came back — the app toasts instead of redrawing). Composite transit/BCycle plans ignore `alt` (their shape is fixed by stop/dock locations). Cost: N+1 plain A* passes (~40 ms each), cached like any plan.
  - `ebike=1` rides at 15 mph instead of 9.4 and pays a quarter of the hill cost. `stress=` re-weights the bike penalties (it never removes an edge, so a route always exists) and also sets what earns a "no bike lane" warning. `stress=balanced` (the default) reproduces the historical stress weights exactly, so omitting the parameter changes nothing about traffic costing. Hills, however, are priced for everyone: terrain reroutes about a third of trips and lengthens most ETAs, so responses DO differ from before v0.5.0 even with no new parameters.
  - Responses carry `climb_ft` (whole trip and per step), `ebike`, and `stress`. `mode` still reports `bike` for an e-bike. v0.6.0 adds `elevation_profile`: `[distance_from_start_m, elevation_ft]` pairs (≤120, downsampled) for plain bike/walk/roll plans — composite transit/BCycle plans omit it (known limitation).
  - **v0.6.0 biases every human-powered mode onto the SRT**: `srt` factors dropped (bike quiet 0.35→0.2, balanced 0.4→0.28, direct 0.5→0.4; walk 0.7→0.55; roll 0.6→0.5). **v0.10.0 deepens it again** (quiet 0.12, balanced 0.18, direct 0.3; walk 0.45, roll 0.45) — a balanced bike detours up to ~5.5× the direct distance for the trail. `stress=balanced` keeps the historical *traffic* weights but no longer reproduces pre-0.6.0 routes byte-for-byte where the trail is competitive.
  - **v0.10.0 graph sources**: OSM paths via Overpass (cycleway/path/pedestrian/non-sidewalk footway + street tunnels like the Springer St tunnel) join the graph — `path` category for true paths, `L` for street tunnels. Posted speed limits (`county.TRA_STREETCL.SPEED`, nearest centerline to each stress segment's midpoint) floor the PCC stress level: ≥45→H, ≥40→MH, ≥35→M (escalation only, ~3.3k segments). PROPOSED bike lanes are excluded from routing (still drawn on the map layer).
  - **v0.11.0**: the OSM ways are a proper pipe — `MapLayers.osm_paths` (4,873 rows, way_id PK, LINESTRING 4326), `plugin:map-layers` `fetch()`, registered by `projects/osm-paths.yaml`, synced daily by the `osm-paths` job in the prod container. Graph build reads the pipe; the direct Overpass fetch + `<output>/osm-paths.json` cache is the never-synced fallback.
  - **v0.12.0 safety pricing**: every street/bike-lane edge carries `(danger, lit)`. Danger = vulnerable-crash score (`Ped.crashes_vulnerable`, fatal 10 / injury 1 / other 0.25 within 100 ft, per 100 m) → weight × up to 3.25; SHARROWs are excluded from the graph. After dark (`?night=0/1` override; response echoes `night`) unlit streets (<1 Duke pole per 100 m, `Ped.lighting`) pay ×1.6 walk/roll ×1.35 bike. New layers: `vulnerable-crashes` (heatmap in the app), `street-lights` (dots). `IX_crashes_vulnerable_geometry` is REQUIRED — without it the graph build seq-scans 29k×.
  - **v0.13.0 gap-fill + honest lane pricing**: county `TRA_STREETCL` segments with no PCC coverage at their midpoint join the graph as `L` floored by posted speed (~3.2k; the Springer-St-east class of hole); bike lanes pay the WORSE of posted speed (≥45 ×4/≥40 ×3/≥35 ×2.5) and the name-matched PCC stress under the paint; OSM `footway` is its own category (bike 0.9/walk 0.8/roll 1.0), no longer trail-cheap. Health check: `python3 scripts/route_sweep.py`.
  - **v0.14.0 rider-scaled lane pricing + trail toggle + renames**: the lane-stress penalty moved from graph build to query time — a lane keeps `LANE_STRESS_RELIEF` (⅓) of the UNDERLYING street's cost *in the rider's own stress factors* (balanced H-lane still nets 4.0; quiet nets 40/3 ≈ 13 — a fixed penalty had left `quiet` as the only tolerance still riding Church St's lane). Edge extras are now `(danger, lit, lane_stress)`. `?trail=0` neutralizes the `srt` factor per request (contextvar + plan-cache key, like `night`). `STREET_RENAMES` maps stale GIS names at ingest/search/road-info (Howe St → Fred Garrett St; searches for the new name are aliased back). SRT graph/step name is now `Prisma Health Swamp Rabbit Trail`. New curated `LANDMARKS` list → `landmarks` layer + search (`The Paperclip` switchbacks at 34.8487,-82.3834).
  - Hills are priced for **every** human-powered mode, from `county."TOP_CONTOUR"` (4 ft contours, SRID 6570, **feet**). Elevation is sampled per graph node by nearest contour, bounded to 2 km so a node outside county coverage is treated as flat rather than borrowing an elevation from miles away. Walk and roll legs steeper than ADA's 1:12 are disclosed as `warn: 'steep'`.
  - **Plans.** `_plan_keys()` turns the selected modes into itineraries: `bike`, `walk`, `roll`, `bcycle`, `bike-transit`, `walk-transit`, `roll-transit`. Every viable one is computed (~40 ms each for a plain A*, ~0.4 s for transit) and the **fastest wins**; the rest come back in `properties.alternatives[]` with real distance/duration, and failures in `properties.unavailable[]`. `plan=<key>` pins one. Results are memoized per (plan, from, to) for `_ROUTE_CACHE_TTL_SECONDS` (120 s) so the app's alternatives chips are instant. Transit access is by bike whenever bike is selected (Greenlink racks; `TRANSIT_BIKE_MAX_M` 5 km catchment vs `TRANSIT_WALK_MAX_M` 1.5 km) and the board step says to load the rack.
  - **Weights.** `MODE_FACTORS` per mode (bike leans hard on stress, walk near-flat, `roll` flatter still) + `MODE_SPEED_M_S` (4.2 / 1.35 / 1.0 m/s). `NO_SIDEWALK_FACTOR` multiplies street edges with no sidewalk beside them: 1.6× walking, **8× rolling**, so a wheelchair route takes a longer sidewalked detour.
  - **Sidewalk presence** is computed at graph-build time, per source segment, by `_sidewalk_exists_sql()`: an indexed `ST_DWithin` (80 ft) against `county.sidewalks` and `city."Sidewalks"`. The street side of the comparison is what gets `ST_Transform`ed so both GiST indexes stay usable — transforming the sidewalk column instead turns this into a 28k × 24k nested loop. City sidewalks needed an index (`IX_city_Sidewalks_geometry`, created 2026-08-01). Coverage: 12.1k of 28.4k stress segments have a sidewalk; by graph edge 16.7k yes / 26.5k no / 1.2k unknown (synthetic connectors).
  - **Disclosure, not silence.** `properties.warn_ranges[]` = `{kind, start, end, distance_m}` index ranges into the LineString where the mode's infrastructure is missing (`no_sidewalk` for walk/roll, `no_bike_lane` for medium-or-worse stress with no lane/trail); `properties.warnings[]` is the per-kind total with a ready-made sentence; each step carries `warn` + `warn_m`. The app draws those ranges dashed red over the route and banners the sentence.
  - **Street fallback.** When the mode's own network can't reach (snap > `ROUTE_SNAP_MAX_M` 400 m, or no connected path), `_route()` retries with flat street weights and a `ROUTE_SNAP_RELAXED_M` (2.5 km) snap, setting `fallback: 'street'` + `fallback_note`. The disclosure stays in the *requested* mode, so the caveats are still about sidewalks/bike lanes.
  - **Bike share** (`_route_bikeshare`): walk → nearest dock with bikes → ride → dock with space → walk, adding `rent`/`dock` maneuvers and `rent_station*` / `dock_station*` props. Stations come from `plugins/bcycle.py` via `mrsm.Plugin('bcycle').module`, so a GBFS outage disables the plan instead of breaking routing.
  - `properties.steps[]` is the turn-by-turn list the app narrates: `{maneuver, instruction, name, distance_m, duration_min, start_index, location, bearing, warn, warn_m}`. Maneuvers: the turn set plus `depart`/`arrive`/`board`/`ride`/`alight`/`rent`/`dock`. Street names come from `pcc.stress_levels.street_name` / `city.BicycleInfrastructure.STREET_NAM` (SRT legs are named "Swamp Rabbit Trail") and are title-cased; legs merge into one step unless the name changes or the bearing swings (`STEP_TURN_MIN_DEG` / `STEP_SAME_STREET_TURN_DEG`).
- `GET /map-layers/search?q=` — bike parking ∪ bus stops ∪ `city."Addresses"` ∪ PCC street names, Nominatim fallback
- `POST /map-layers/feedback` — generic report → `MapLayers.layer_feedback` (photos → `<root>/uploads/map-layers/`)

### `plugins/bike-parking.py`
- `GET /bike-parking/data.geojson` — 283 racks
- `POST /bike-parking/submit` — multipart `spot_name, lat, lon, feedback, photo` → `BikeParking.parking_feedback`

Both POST endpoints: **no auth, no rate limit, no moderation column** — known risk, revisit before public launch.

### `plugins/walk-audit.py` (added 2026-07)
- `POST /walk-audit/submit` — category + comment + photo + lat/lon → nearest-road owner resolution via `Roads.roads` → stored **synchronously** in `app/reports/WalkAudit` (so the app can refresh the reports layer and see the new pin), then a staff-only notification email is sent off-thread. **Reports are never forwarded to municipal offices**; owner resolution is stored for analysis and for the app's contact card. SMTP + recipient come from Meerschaum config `plugins:walk-audit:{smtp,notify}` — locally via `mrsm-compose.yaml` (interpolated from `.env`, repo is public), in prod from the API container's `/meerschaum/config/plugins.json`. The old `/meerschaum/.env` hack is gone.
- `GET /walk-audit/reports.geojson` — submitted issues layer
- `GET /map-layers/road-info?lat=&lon=` — nearest road contact card (WOTR-on-tap)

## Gaps / dead weight

1. **GTFS**: nothing — task `projects/transit.yaml` (stretch).
2. **Lane counts**: no column; PCC stress already encodes the derived risk; Replica/OSM would be sources if needed.
3. `Sidewalks.county_sidewalks` empty; `projects/gcgis.yaml` has an empty-parameter stub pipe; `plugins/app.py` dead Flet prototype.
4. City sidewalks are polygons (heavy) — prefer `county.sidewalks` lines + `streets_with_sidewalks` for rendering.
5. `projects/scdps-incidents.yaml` targets `sql:traffic`→`sql:main`, legacy schema — not part of the `sql:bwg` graph.

## Mobile app (app-native/)

Native Flutter (replaces Flet `bwg_app/`). MapLibre GL consumes the GeoJSON endpoints above directly. Signing: `app-native/keystore.properties` + `sra-upload.keystore` (gitignored; backup in Google Drive → shared SRA upload key, SHA1 `537F9A88AAB6623CFA91F0FBABCE6F95E705A843`). Conventions copied from `~/projects/trail-counter/app-native` (flat `lib/`, provider + single AppState, Dio single-chokepoint client, version = pubspec `x.y.z+N`, `gplay` CLI for Play).

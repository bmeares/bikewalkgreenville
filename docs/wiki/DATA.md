# BWG Data Knowledge Base

Inventory of `sql:bwg` (prod TimescaleDB behind `bwg.mrsm.io`), the pipes that fill it, and the HTTP endpoints the mobile app consumes. 210 pipes, 322 tables. Compose sources: `mrsm-compose.yaml` + `projects/*.yaml`.

## CRS regimes (the #1 integration gotcha)

| SRID | What | Where |
|---|---|---|
| 4326 (WGS84) | app-ready | crash points (`SCDPS.*`, `Ped.*`), `SRT.*`, `public.srt`, `replica.*`; `BikeParking` is plain lat/lon floats |
| 6570 (SC ft) | state plane | `Roads.*`, `pcc.*`, `county.*`, `gcgis.*`, `Boundaries.*`, `city.BusRoutes/BusStops` |
| 3361 (SC m) | HARN | most `city.*` (Sidewalks, BicycleInfrastructure, Streets, Trails), `Parking.*` |

Anything served to the app must `ST_Transform(geom, 4326)`. `pcc.stress_levels.geojson` (jsonb) is pre-transformed. `geometry_columns` lies (`srid=0`) for several `Roads`/`Ped` tables — trust the data, not the view.

## Feature → data map

| App feature | Tables | Rows | Notes |
|---|---|---|---|
| Bike stress | `pcc.stress_levels` | 28,445 | `stress_level` L/ML/M/MH/H (22154/112/3372/1977/830); per-level split tables; statewide `stress_levels_sc` 402k |
| Bike lanes/infra | `city.BicycleInfrastructure` | 919 | `STATUS` EXISTING/PROPOSED, `BIKE_TYPE` BIKELANE 588 / SHARROW 322 / GREENWAY 9; city limits only |
| Trails / SRT | `city.Trails` (542), `city.ApprovedTrails` (72), `gcgis."PRISMA Health Swamp Rabbit Trail"` (305), `SRT.segments*` | | `SRT.segments_owners` carries maintenance contacts per segment |
| Sidewalks | `county.sidewalks` (18,493 lines), `Sidewalks.sidewalks` (5,325 city **polygons**), `Sidewalks.streets_with_sidewalks` (3,103) | | `Sidewalks.county_sidewalks` is EMPTY (dead) |
| Bus/transit | `transit.routes` (16), `transit.stops` (997, w/ routes-served + Point 4326), `transit.route_shapes` (31 LineString 4326 w/ official colors) | | Greenlink GTFS (`plugins/gtfs.py`, `projects/transit.yaml`, feed `gtfs.greenlink.cadavl.com`); supersedes stale `city.BusRoutes`/`BusStops`. Schedules (stop_times) not yet ingested — headways/frequency a future step. Synced daily in prod by the `transit` job inside `mrsm-api-bwg-1`. |
| Bike parking | `BikeParking.parking_locations` | 283 | OSM Overpass `amenity=bicycle_parking`; lat/lon floats |
| Bike repair stations | `BikeParking.repair_stations` | ~few | OSM `amenity=bicycle_repair_station` (added 2026-07) |
| Roads/ownership (WOTR) | `Roads.roads` | 35,204 | Owner + Email/Phone/Online Form denormalized per segment; `Roads.contact_info` (20) is the municipal contact lookup (Greenville Cares `cares@greenvillesc.gov`, SCDOT MWRO, six cities, GCPRT…) |
| Speed limits | `county.TRA_STREETCL.SPEED` (35,185), `city.Streets.SPEED` (5,745) | | **No lane-count column anywhere**; observed speeds in `replica."annual-speeds"` (66k, OSM-keyed p50/p85/p95) |
| Collisions | `Ped.crashes_vulnerable` (1,650) + pedalcycle/pedestrian splits; `SCDPS.sql_bwg_collisions_*` map-ready points | | rich attrs: lighting, junction, contributing factor |
| Boundaries | `Boundaries.boundaries` (6 municipalities), `county_council_districts` (12) | | |
| Bonus | `deflock.cameras_greenville` (83 ALPR), `brokenspoke.neighborhood_overall_scores` (23 BNA), `duke.lighting` (63k streetlights), `Events.events_greenville` | | |

## HTTP endpoints (served by Meerschaum Web plugins at bwg.mrsm.io)

### `plugins/map-layers.py`
- `GET /map-layers/index.json` — layer registry (bus-routes, bus-stops, bike-lanes, sidewalks-city, sidewalks-county, srt, bike-stress)
- `GET /map-layers/{layer}.geojson` — dissolved overview; `?bbox=&zoom=` → per-feature detail (limit 4000)
- `GET /map-layers/route?from=lat,lon&to=lat,lon&mode=bike|walk|transit` — in-process A* over SRT + bike lanes + PCC stress; `route-stats.json`. Per-mode edge weights (`MODE_FACTORS`: bike leans hard on stress, walk is near-flat) and speeds (`MODE_SPEED_M_S` 4.2 / 1.35 m/s). `mode=transit` = walk to a Greenlink stop → ride the route shape → walk to destination (`_route_transit`): stop pairs share a route (`transit.stops.routes`), ride geometry sliced from `transit.route_shapes`, flat `TRANSIT_WAIT_MIN` (8 min — no stop_times yet), adds `board`/`ride`/`alight` maneuvers plus `route`, `board_stop`, `alight_stop`, `route_color` props. Response `properties.steps[]` is the turn-by-turn list the app narrates: `{maneuver, instruction, name, distance_m, duration_min, start_index, location, bearing}`. Street names come from `pcc.stress_levels.street_name` / `city.BicycleInfrastructure.STREET_NAM` (SRT legs are named "Swamp Rabbit Trail") and are title-cased; legs merge into one step unless the name changes or the bearing swings (`STEP_TURN_MIN_DEG` / `STEP_SAME_STREET_TURN_DEG`).
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

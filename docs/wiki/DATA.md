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

## Auth model (release 1.22, `plugins/bwg-auth.py` v0.1.0)

Browsing, routing, navigation, ride recording, events and group rides stay **anonymous**. Every public write (contributions, votes, rollbacks, reports, photos, feedback, saved routes, profile) needs a signed-in account.

| Aspect | Rule |
|---|---|
| Flow | Passwordless: `POST /bwg/auth/request-code {email}` → 6-digit code emailed (subject "Your Bike Walk Greenville sign-in code", sent with walk-audit's SMTP config `plugins:walk-audit:smtp`) → `POST /bwg/auth/verify {email, code}` → token |
| Username | == lowercased email. Must match `^[^@\s]+@[^@\s]+\.[^@\s]+$` **and** Meerschaum `valid_username` (alnum plus `_ - . @` only, so **no `+` addresses**; ≤ 60 chars) → 400 otherwise |
| User record | Created on first successful verify on the **API instance** connector (same one `/login` and the Dash session use): random password, `type='user'`, `attributes = {'scopes': ['bwg'], 'bwg': {…}}`. `attributes.bwg` holds `display_name`, `settings`, `disclaimer_version`, `disclaimer_accepted_at`, `banned` |
| Code | `sha256("<email>:<code>")` stored, 10 min TTL, max 5 wrong guesses per code, single use; only the newest code for an email is valid |
| Code rate limits | 3 codes / 15 min per email, 20 codes / hour per IP → 429 (counted from `BwgAuth.login_codes`, so they survive restarts) |
| Token | Meerschaum API key (`mrsm-key:…`), scope `bwg`, expiry **1 year**, label `bwg-app <date> <hex>`. Sent as `Authorization: Bearer mrsm-key:…`. Keys scoped `*` are also accepted |
| Admin | `is_admin` = the Meerschaum user's `type == 'admin'` on the API instance, re-read on each Bearer lookup |
| Banned | `attributes.bwg.banned = true` → **403** on every write (`require_user(write=True)`); reads still work. Admins cannot be banned (demote first) |
| Caching | Bearer → user resolved through a 60 s in-process cache: a ban / promotion / revocation made in another process lands within a minute; sign-out on this process evicts immediately |
| Shared helpers | Other plugins load `mrsm.Plugin('bwg-auth').module` (sibling-file fallback in tests): `bwg_user(request)` → `{username, is_admin, banned, token_id}` or None; `require_user(request, write=True)` → 401/403; `moderation_check(text)` → `'ok'`/`'held'` |
| Text filter | `moderation_check` holds text with a word from the small profanity list (leetspeak-folded), ≥ 2 URLs, > 50 % capitals on > 20 chars, or the same text > 3 times in 10 min (in-process counter) |

Emails are **never published**: `username` columns are private; public payloads expose only `mine` (bool) and the moderation console masks addresses (`b…t@x.org`).

## HTTP endpoints (served by Meerschaum Web plugins at bwg.mrsm.io)

**Auth column legend:** *anon* = no header needed; *Bearer* = signed-in `bwg` token (401 without one, 403 if banned on writes); *admin Bearer* = Bearer whose user is a Meerschaum `admin` (403 otherwise); *admin session* = Meerschaum web login cookie of an admin. Errors are JSON `{error}` everywhere except `group-rides` and `/r/{code}`, which raise FastAPI `HTTPException` → `{detail}`.

**Anonymous before 1.22, Bearer now:** `POST /map-layers/submit-point`, `/map-layers/community/rollback`, `/map-layers/community/confirm`, `/map-layers/feedback`, `/walk-audit/submit`, `/walk-audit/dismiss`, `/bike-parking/submit`. Old app builds (≤ 1.21.x) get 401 on all of them.

### `plugins/bcycle.py`
- `GET /bcycle/stations.geojson` — Greenville BCycle docks with **live** availability, merged from the system's GBFS 1.1 feed (`bcycle_greenville`: `station_information` + `station_status`). 13 stations, all downtown-ish. Props: `name`, `address`, `bikes`, `ebikes`, `docks`, `is_renting`, `availability` (pre-formatted line), `rental_uri` (per-station Android deep link), `short_id` (the number on the kiosk). Cached 45 s in-process, served `Cache-Control: no-store`, and serves stale-over-empty if GBFS blips. Falls back to the `BCycle.stations` pipe (locations only, `projects/bcycle.yaml`) when the feed is unreachable.
- `GET /bcycle/system.json` — system name/url/phone/email plus `app_discovery_uri` (`bcycle://`) and `app_store_uri`, the chain the app walks to hand a rider off to the BCycle app.
- `get_stations()` is also imported by `map-layers.py` for the `bcycle` routing plan.

### `plugins/map-layers.py` — layers, search, routing
- `GET /map-layers/index.json` — layer registry (community, bus-routes, bus-stops, bike-lanes, sidewalks-city, sidewalks-county, srt, **sidewalks** (merged), **parking-landuse**, custom-paths, landmarks, **bike-businesses**, vulnerable-crashes, street-lights, bike-stress)
- `GET /map-layers/{layer}.geojson` — dissolved overview; `?bbox=&zoom=` → per-feature detail (limit 4000). Builder layers (v0.6.0): `sidewalks` = county lines ∪ city lines >80 ft from any county line (the app now shows ONE sidewalks toggle; the per-source layers stay for back-compat); `parking-landuse` = DTMP surface lots (`Parking.dtmp_parking`, FEAT_CODE 121/122) + garage footprints (`Parking.parking_facilities_greenville`), `kind` = lot|garage; `bike-businesses` = the hand-curated `BIKE_BUSINESSES` list in the plugin (edit + redeploy to add one).
- `GET /map-layers/parking-garages.geojson` — downtown garages with latest occupancy from the cached `Parking.garages_counts_map` pipe (`name`, `capacity`, `occupied`, `percent_occupied`, `availability`, `as_of`). 60 s in-process cache, `Cache-Control: no-store`. **Registered before the `{layer}.geojson` route on purpose — Starlette matches in declaration order.**
- `GET /map-layers/route?from=lat,lon&to=lat,lon&modes=bike,walk,transit[&roll=1][&bcycle=1][&plan=<key>][&ebike=1][&stress=quiet|balanced|direct][&alt=1..3][&night=0/1][&trail=0][&community=0]` — **multi-modal** directions over an in-process A* graph (SRT + bike lanes + PCC stress); `route-stats.json` for graph health. The legacy single `?mode=bike|walk|transit` still works. `trail=0` (v0.14.0) turns off the SRT bias — the trail prices like a plain calm street; the response echoes `trail`. `community=0` (v0.20.0) turns off the community-routes bias — rider-drawn shortcuts/route suggestions price as ordinary `path` instead of `COMMUNITY_PREF_FACTOR` (½) of it; the response echoes `community`. Routes also carry `community_ranges` (v0.20.0: `[{name,start,end,distance_m}]` index ranges of rider-drawn stretches) and every street chunk within `REPORT_RADIUS_M` (25 m) of an open report (walk-audit reports not dismissed and not held + active, non-held community `access-issue`/`crossing` points, `REPORT_CATEGORIES`) costs `REPORT_PENALTY_FACTOR` (3x) through the danger multiplier at graph build.
  - **Alternate routes (v0.8.0).** `alt=N` with `plan` pinned to a plain plan (`bike`/`walk`/`roll`) returns that plan's Nth alternate: each pass re-runs A* with the previous passes' edges costing `ALT_AVOID_FACTOR` (1.5×), so a genuinely different street wins when one exists. Response carries `alt` and `alt_distinct`; `alt_distinct: false` means no different way exists (the same route came back — the app toasts instead of redrawing). Composite transit/BCycle plans ignore `alt` (their shape is fixed by stop/dock locations). Cost: N+1 plain A* passes (~40 ms each), cached like any plan.
  - `ebike=1` rides at 15 mph instead of 9.4 and pays a quarter of the hill cost. `stress=` re-weights the bike penalties (it never removes an edge, so a route always exists) and also sets what earns a "no bike lane" warning. `stress=balanced` (the default) reproduces the historical stress weights exactly, so omitting the parameter changes nothing about traffic costing. Hills, however, are priced for everyone: terrain reroutes about a third of trips and lengthens most ETAs, so responses DO differ from before v0.5.0 even with no new parameters.
  - Responses carry `climb_ft` (whole trip and per step), `ebike`, and `stress`. `mode` still reports `bike` for an e-bike. v0.6.0 adds `elevation_profile`: `[distance_from_start_m, elevation_ft]` pairs (≤120, downsampled) for plain bike/walk/roll plans — composite transit/BCycle plans omit it (known limitation).
  - **v0.6.0 biases every human-powered mode onto the SRT**: `srt` factors dropped (bike quiet 0.35→0.2, balanced 0.4→0.28, direct 0.5→0.4; walk 0.7→0.55; roll 0.6→0.5). **v0.10.0 deepens it again** (quiet 0.12, balanced 0.18, direct 0.3; walk 0.45, roll 0.45) — a balanced bike detours up to ~5.5× the direct distance for the trail. `stress=balanced` keeps the historical *traffic* weights but no longer reproduces pre-0.6.0 routes byte-for-byte where the trail is competitive.
  - **v0.10.0 graph sources**: OSM paths via Overpass (cycleway/path/pedestrian/non-sidewalk footway + street tunnels like the Springer St tunnel) join the graph — `path` category for true paths, `L` for street tunnels. Posted speed limits (`county.TRA_STREETCL.SPEED`, nearest centerline to each stress segment's midpoint) floor the PCC stress level: ≥45→H, ≥40→MH, ≥35→M (escalation only, ~3.3k segments). PROPOSED bike lanes are excluded from routing (still drawn on the map layer).
  - **v0.11.0**: the OSM ways are a proper pipe — `MapLayers.osm_paths` (4,873 rows, way_id PK, LINESTRING 4326), `plugin:map-layers` `fetch()`, registered by `projects/osm-paths.yaml`, synced daily by the `osm-paths` job in the prod container. Graph build reads the pipe; the direct Overpass fetch + `<output>/osm-paths.json` cache is the never-synced fallback.
  - **v0.12.0 safety pricing**: every street/bike-lane edge carries `(danger, lit)`. Danger = vulnerable-crash score (`Ped.crashes_vulnerable`, fatal 10 / injury 1 / other 0.25 within 100 ft, per 100 m) → weight × up to 3.25; SHARROWs are excluded from the graph. After dark (`?night=0/1` override; response echoes `night`) unlit streets (<1 Duke pole per 100 m, `Ped.lighting`) pay ×1.6 walk/roll ×1.35 bike. New layers: `vulnerable-crashes` (heatmap in the app), `street-lights` (dots). `IX_crashes_vulnerable_geometry` is REQUIRED — without it the graph build seq-scans 29k×.
  - **v0.13.0 gap-fill + honest lane pricing**: county `TRA_STREETCL` segments with no PCC coverage at their midpoint join the graph as `L` floored by posted speed (~3.2k; the Springer-St-east class of hole); bike lanes pay the WORSE of posted speed (≥45 ×4/≥40 ×3/≥35 ×2.5) and the name-matched PCC stress under the paint; OSM `footway` is its own category (bike 0.9/walk 0.8/roll 1.0), no longer trail-cheap. Health check: `python3 scripts/route_sweep.py`.
  - **v0.14.0 rider-scaled lane pricing + trail toggle + renames**: the lane-stress penalty moved from graph build to query time — a lane keeps `LANE_STRESS_RELIEF` (⅓) of the UNDERLYING street's cost *in the rider's own stress factors* (balanced H-lane still nets 4.0; quiet nets 40/3 ≈ 13 — a fixed penalty had left `quiet` as the only tolerance still riding Church St's lane). Edge extras are now `(danger, lit, lane_stress)`. `?trail=0` neutralizes the `srt` factor per request (contextvar + plan-cache key, like `night`). `STREET_RENAMES` maps stale GIS names at ingest/search/road-info (Howe St → Fred Garrett St; searches for the new name are aliased back). SRT graph/step name is now `Prisma Health Swamp Rabbit Trail`. New curated `LANDMARKS` list → `landmarks` layer + search (`The Paperclip`, v0.14.1: on the trail at the top of the switchbacks, 34.8509,-82.3834; the app draws landmarks as text labels, not pins).
  - **v0.15.0 grade separation**: tunnel ways (OSM street rows, row tuple element 8) key their graph nodes into a `('T', …)` namespace so the 12 m grid snap can never fuse them with surface geometry crossing above (the Springer-tunnel-onto-Church-St left turn); portals rejoin via connectors to the nearest chunk endpoint of a SAME-base-named street (`_street_base`). `TUNNEL_ROOF_ROWS` drops surface rows that are really a tunnel's roof (PCC's Springer stub across Church) by segment-bbox overlap at ingest.
  - **v0.16.0 grade separation, rounds 2–3 + trail-tier streets**: `GRADE_SEPARATED_ROWS` CLIPS (not drops) the vertices of side streets the Church St embankment severs (Wakefield, Judson — TIGER/OSM digitize at-grade crossings that don't exist); `_add_connector` refuses any junction/stitch connector whose segment crosses a tunnel-roof/grade-separated window (the Church bike lane's chunk end, ON the bridge, was 10 m from the west portal and got ramped down onto Springer — T-namespaced portals stay exempt). `TRAIL_TIER_STREETS` re-categorizes car-free trail-access streets (`FURMAN COLLEGE`) as `srt` at build; CUSTOM_PATHS entries may carry `route_coords` (graph) separate from `coords` (drawn line); walk-audit gains a `missing-shortcut` report category; parking-garages clamps occupancy outside [0, capacity].
  - Hills are priced for **every** human-powered mode, from `county."TOP_CONTOUR"` (4 ft contours, SRID 6570, **feet**). Elevation is sampled per graph node by nearest contour, bounded to 2 km so a node outside county coverage is treated as flat rather than borrowing an elevation from miles away. Walk and roll legs steeper than ADA's 1:12 are disclosed as `warn: 'steep'`.
  - **Plans.** `_plan_keys()` turns the selected modes into itineraries: `bike`, `walk`, `roll`, `bcycle`, `bike-transit`, `walk-transit`, `roll-transit`. Every viable one is computed (~40 ms each for a plain A*, ~0.4 s for transit) and the **fastest wins**; the rest come back in `properties.alternatives[]` with real distance/duration, and failures in `properties.unavailable[]`. `plan=<key>` pins one. Results are memoized per (plan, from, to) for `_ROUTE_CACHE_TTL_SECONDS` (120 s) so the app's alternatives chips are instant. Transit access is by bike whenever bike is selected (Greenlink racks; `TRANSIT_BIKE_MAX_M` 5 km catchment vs `TRANSIT_WALK_MAX_M` 2 km as of v0.21.0, was 1.5 km; both overridable at request time via Meerschaum config `plugins:map-layers:transit:{walk_max_m,bike_max_m}`) and the board step says to load the rack. When no stop is in reach the plan fails with `No bus stops within <mi> (<km> km) biking|walking distance of your start|destination.`
  - **Weights.** `MODE_FACTORS` per mode (bike leans hard on stress, walk near-flat, `roll` flatter still) + `MODE_SPEED_M_S` (4.2 / 1.35 / 1.0 m/s). `NO_SIDEWALK_FACTOR` multiplies street edges with no sidewalk beside them: 1.6× walking, **8× rolling**, so a wheelchair route takes a longer sidewalked detour.
  - **Sidewalk presence** is computed at graph-build time, per source segment, by `_sidewalk_exists_sql()`: an indexed `ST_DWithin` (80 ft) against `county.sidewalks` and `city."Sidewalks"`. The street side of the comparison is what gets `ST_Transform`ed so both GiST indexes stay usable — transforming the sidewalk column instead turns this into a 28k × 24k nested loop. City sidewalks needed an index (`IX_city_Sidewalks_geometry`, created 2026-08-01). Coverage: 12.1k of 28.4k stress segments have a sidewalk; by graph edge 16.7k yes / 26.5k no / 1.2k unknown (synthetic connectors).
  - **Disclosure, not silence.** `properties.warn_ranges[]` = `{kind, start, end, distance_m}` index ranges into the LineString where the mode's infrastructure is missing (`no_sidewalk` for walk/roll, `no_bike_lane` for medium-or-worse stress with no lane/trail); `properties.warnings[]` is the per-kind total with a ready-made sentence; each step carries `warn` + `warn_m`. The app draws those ranges dashed red over the route and banners the sentence.
  - **Street fallback.** When the mode's own network can't reach (snap > `ROUTE_SNAP_MAX_M` 400 m, or no connected path), `_route()` retries with flat street weights and a `ROUTE_SNAP_RELAXED_M` (2.5 km) snap, setting `fallback: 'street'` + `fallback_note`. The disclosure stays in the *requested* mode, so the caveats are still about sidewalks/bike lanes.
  - **Bike share** (`_route_bikeshare`): walk → nearest dock with bikes → ride → dock with space → walk, adding `rent`/`dock` maneuvers and `rent_station*` / `dock_station*` props. Stations come from `plugins/bcycle.py` via `mrsm.Plugin('bcycle').module`, so a GBFS outage disables the plan instead of breaking routing.
  - `properties.steps[]` is the turn-by-turn list the app narrates: `{maneuver, instruction, name, distance_m, duration_min, start_index, location, bearing, warn, warn_m}`. Maneuvers: the turn set plus `depart`/`arrive`/`board`/`ride`/`alight`/`rent`/`dock`. Street names come from `pcc.stress_levels.street_name` / `city.BicycleInfrastructure.STREET_NAM` (SRT legs are named "Swamp Rabbit Trail") and are title-cased; legs merge into one step unless the name changes or the bearing swings (`STEP_TURN_MIN_DEG` / `STEP_SAME_STREET_TURN_DEG`).
- `GET /map-layers/community.geojson` — the `community` builder layer (served `no-store`, never pregenerated): active contributions only (not rolled back, not replaced, not `held`/`rejected`). Props: `id`, `name`, `comment`, `category`, `upvotes`, `downvotes`, `photo_url` (null unless the photo is `approved`). The old `confirmations` property is **gone** (v0.21.0) — use `upvotes`/`downvotes`.
- `GET /map-layers/search?q=` — bike parking ∪ bus stops ∪ `city."Addresses"` ∪ PCC street names, Nominatim fallback
- `GET /map-layers/road-info?lat=&lon=` — nearest road contact card (WOTR-on-tap)
- `POST /map-layers/feedback` — **Bearer**. Multipart `layer, name, lat, lon, props, feedback, photo` → `MapLayers.layer_feedback` (now with `username`; photos `.jpg/.jpeg/.png/.webp/.heic` only → `<root>/uploads/map-layers/`, never served publicly). → `{ok, id}`; 400 bad photo type. Write is non-blocking. No rate limit, no photo size cap, no moderation columns.

### `plugins/map-layers.py` — community (v0.21.0)

Storage is the append-only `MapLayers.community_revisions`; nothing is updated in place. Row `category` is either a contribution category (`SUBMISSION_CATEGORIES`: bike-parking, repair-station, water-fountain, bike-business, shortcut, route-suggestion, map-correction, access-issue, crossing, no-entry, other) or a meta row: `rollback` (`reverts` = target), `confirm` (legacy anonymous up vote, `confirms` = target, `voter` = per-install token), `vote` (`confirms` = target, `vote` = up|down|null, `username`), `moderate` (`confirms` = target, sets `status`/`photo_status`). `submit-point`, `vote`/`confirm` and `rollback` share ONE in-process limiter: **10 requests / hour / IP combined** (429).

| Method + path | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `POST /map-layers/submit-point` | Bearer (was anon) | multipart `category`, `name`, `comment`, `lat`, `lon`, `photo?`, `geometry?` (GeoJSON Point/LineString/Polygon text, ≤ 200 vertices, in service bounds; LineString 2 m–30 km; Polygon only and always for `no-entry`, 4 m²–25 km²), `replaces?` (id of the active version being edited) | `{ok, id, status: 'published'\|'held', photo_status: 'pending'\|null}` | 400 category/location/empty name+comment/photo type (jpg jpeg png webp heic)/geometry; 409 `replaces` no longer active; 413 photo > 8 MB; 429; 503 |
| `POST /map-layers/community/vote` | Bearer | JSON `{id, up: bool}` | `{ok, up, down, mine: 'up'\|'down'\|null, confirmations}` (`confirmations` = `up`, compat only). Same vote again withdraws it, the other flips it; one live vote per user per contribution. `mine` is only ever learned from this response | 400 bad body; 409 target not active; 429; 503 |
| `POST /map-layers/community/confirm` | Bearer (was anon `{id, voter}`) | JSON `{id}` (`voter` now ignored) | same as vote; alias of `up=true` but **not** a toggle | 409 already up-voted / target not active; 400; 429 |
| `POST /map-layers/community/rollback` | Bearer (was anon) | JSON `{id \| ids: [...≤200], reason (1–2000 chars)}`, body ≤ 4 KB | `{ok, removed, skipped: [ids]}` | 400; 403 non-admin listing someone else's contribution; 409 nothing listed is active; 413; 429; 503 |
| `POST /map-layers/community/moderate` | admin Bearer | JSON `{id \| ids (≤200), status?: 'published'\|'rejected', photo_status?: 'approved'\|'rejected'\|'pending'}` | `{ok, message}` | 400 unknown decision / nothing to change / no such contribution; 401/403 |
| `GET /map-layers/community/history` | anon (Bearer optional) | — | `{revisions: [{id, ts, ts_display, category, name, comment, reverts, replaces, type: add\|edit\|rollback\|confirm, geometry, photo_url, mine, active}]}` newest first. `vote`/`moderate` rows and held/rejected text excluded; `mine` = caller authored it (false when signed out); `photo_url` approved only | 500/503 on DB read failure |
| `GET /map-layers/photos/{filename}` | anon | — | the file, `Cache-Control: public, max-age=86400` | 404 unless the filename belongs to a contribution whose photo is `approved` |

**Rollback rules.** Only ACTIVE ids count (others go to `skipped`). Each target's whole `replaces` chain is reverted so an older version never resurfaces — **for admins**. A non-admin may list only their own contributions, and the chain walk stops at the first version someone else wrote, so that earlier version resurfaces. Rollback rows carry the actor's `username`.

### `plugins/map-layers.py` — saved routes (private, per user)

| Method + path | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `GET /bwg/routes` | Bearer (read; banned users allowed) | — | `{routes: [{id, name, from_lat, from_lon, to_lat, to_lon, modes, stress, distance_m, duration_min, created, geometry}]}` newest first; `geometry` parsed GeoJSON LineString or null | 503 |
| `POST /bwg/routes` | Bearer | JSON `{name (1–80), from_lat, from_lon, to_lat, to_lon (service bounds), modes? ('bike,walk,…' from bike walk roll transit ebike bcycle; default bike), stress? (quiet\|balanced\|direct), distance_m?, duration_min?, geometry? (LineString or Feature, ≤ 10k coords)}`, body ≤ 600 KB | the saved route (same shape as a list item) | 400; 409 at 200 routes; 413; 503 |
| `DELETE /bwg/routes/{id}` | Bearer | — | `{ok: true}` | 404 not yours / unknown; 503 |

### `plugins/bwg-auth.py` (v0.1.0)

| Method + path | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `POST /bwg/auth/request-code` | anon | JSON `{email}` | `{ok: true, message}` — neutral message, no user enumeration | 400 bad email; 429; 503 |
| `POST /bwg/auth/verify` | anon | JSON `{email, code}` | `{token, email, display_name, is_admin}` | 401 bad/expired/used/too many attempts; 503 |
| `GET /bwg/auth/me` | Bearer (read) | — | `{email, display_name, is_admin, disclaimer_version, disclaimer_accepted_at, settings}` (`settings` = opaque app-owned JSON object) | 401 |
| `PUT /bwg/auth/me` | Bearer | JSON `{display_name? (≤ 40, profanity → 400), settings? (object ≤ 64 KB), disclaimer_version? (int 1–999; a new value stamps `disclaimer_accepted_at`)}` | same as GET | 400; 401/403; 413 settings too large; 503 |
| `DELETE /bwg/auth/token` | Bearer | — | `{ok: true}` — revokes this device's token only | 401; 503 |

### `plugins/moderation.py` (v0.2.0)

| Method + path | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `GET /bwg/moderation/pending-count` | admin Bearer | — | `{photos, held}` — `photos` = pending community + walk-audit + bike-parking photos; `held` = held **community** text only | 401/403; 503 |
| `GET /bwg/moderation/export.{gpx\|osm\|geojson\|csv}` | admin Bearer | query `category`, `status` (published default \| held \| rejected \| removed \| all), `start`/`end` (YYYY-MM-DD, Eastern, inclusive), `q` (≤ 200), `geometry` (Point\|LineString\|Polygon) | attachment `bwg-community-<date>.<fmt>` | 400 bad filter; 404 bad format; 401/403 |
| `GET /bwg/moderation/photo/{filename}` | admin Bearer, admin session cookie (`mrsm-session-id`), or a console-signed `?exp=&sig=` (HMAC, 1 h) | — | any known photo incl. pending (community, walk-audit, bike-parking), `private, max-age=3600` | 403; 404 |
| `/dash/moderation` | admin session | Dash console | see Moderation model | non-admins see a "403: moderators only" card with a sign-in link |

### `plugins/group-rides.py` (v0.1.0) — anonymous

Riders are identified only by a per-member `member_token` (returned once; `sha256` stored). Codes: 4 letters from `ABCDEFGHJKMNPRTUVWXY`. Create + join share a 30 / hour / IP limit. Rider/ride names ≤ 30 chars; names held by `moderation_check` (or blank) fall back to `Rider N` / `Group ride`. Rides end lazily after **6 h** or **30 min** without a leader ping; members silent > 5 min drop out of lists.

| Method + path | Request | Response | Errors |
|---|---|---|---|
| `POST /group-rides` | `{name, rider_name, lat, lon}` | `{code, ride_id, member_id, member_token, share_url}`; caller leads; `share_url = https://bwg.mrsm.io/bwg-app/?ride=CODE` | 400 lat/lon; 429; 503 |
| `GET /group-rides/nearby?lat&lon` | — | bare list `[{code, name, leader_name, distance_m, members}]`, nearest first: leader within 305 m and pinged < 2 min ago | 400 |
| `POST /group-rides/{code}/join` | `{rider_name, lat, lon}` | `{ride_id, member_id, member_token, name, ride_name, share_url}` | 404 unknown/ended; 400; 429 |
| `POST /group-rides/{code}/ping` | `{member_token, lat, lon, heading?, speed?}` | `{ended: false, ride_name, share_url, leader_id, route (Feature\|null), members: [{id, name, lat, lon, heading, updated, is_leader}]}`; an ended ride → 200 `{ended: true, leader_id, route: null, members: []}` | 400; 401 bad token; 404 unknown code |
| `PUT /group-rides/{code}/route` | `{member_token, route: Feature<LineString>}` (≤ 200 KB) | `{ok: true}` | 400; 401; 403 not leader; 404; 413 |
| `POST /group-rides/{code}/leave` | `{member_token}` | `{ok: true}`; revokes the token; the **leader leaving ends the ride** | 401; 404 |
| `POST /group-rides/{code}/end` | `{member_token}` | `{ok: true}` | 401; 403 not leader; 404 |

### `plugins/bwg-events.py` (v0.1.0)

- `GET /bwg/events.json?days=60` — anon, `days` 1–90 (422 outside). → `{events: [{uid, start, end, all_day, title, location, description, html_link, luma_url}]}` (UTC ISO strings) sorted by start, events that have not ended and start within `days`; only the newest sync batch is served (cancelled/moved events drop out without deletes). `Cache-Control: public, max-age=300`. `luma_url` = first public `lu.ma`/`luma.com` link (organizer `event/manage` links skipped). Source ICS overridable via `plugins:bwg-events:ics_url`.
- Prod job (API container): `mrsm register pipe -c plugin:bwg-events -m events -i sql:bwg` then `mrsm sync pipes -c plugin:bwg-events -m events -i sql:bwg --loop --min-seconds 3600 --name bwg-events -d`.

### `plugins/bwg-app.py` (v0.2.0)

- `/bwg-app/` — static Flutter web bundle; `/dash/app` iframes it.
- `GET /r/{code}` — anon short group-ride link: 302 → `https://bwg.mrsm.io/bwg-app/?ride=CODE` (uppercased; 404 unless 4 letters A–Z).


### `plugins/bike-parking.py` (v0.3.0)

| Method + path | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `GET /bike-parking/data.geojson` | anon | — | 283 racks (`name`, `capacity`, `address`) | — |
| `GET /bike-parking/repair-stations.geojson` | anon | — | OSM repair stands | — |
| `POST /bike-parking/submit` | Bearer (was anon) | multipart `spot_name (≤ 200), lat, lon, feedback (≤ 2000), photo?` | `{ok, id, status: 'published'\|'held', photo_status: 'pending'\|null}` → `BikeParking.parking_feedback` (non-blocking write) | 400 text length / photo type; 413 photo > 8 MB; 401/403 |
| `GET /bike-parking/photos/{filename}` | anon | — | approved photo only | 404 |

Still missing on `/bike-parking/submit`: rate limit and lat/lon validation.

### `plugins/walk-audit.py` (v0.4.0)

| Method + path | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `POST /walk-audit/submit` | Bearer (was anon) | multipart `category, comment (≤ 2000), lat, lon (service bounds 34.58–35.10, −82.65 to −82.10), photo?` | `{ok, id, road_name, owner, owner_email, owner_form, status: 'published'\|'held', photo_status}` | 400; 413 photo > 8 MB; 429 (10 / hour / IP, own limiter) |
| `GET /walk-audit/reports.geojson` | anon | — | reports minus dismissed and held; props `id, category, label, comment, road_name, owner, ts, photo_url` (approved only) | — |
| `GET /walk-audit/categories.json` | anon | — | `{categories: [{id, label}]}` | — |
| `POST /walk-audit/dismiss` | Bearer (was anon) | JSON `{id, reason (1–2000)}`, ≤ 4 KB | `{ok: true}` → `WalkAudit.report_edits` (`action='dismiss'`, `username`) | 400; 404 unknown/held; 409 already dismissed; 413; 503 |
| `GET /walk-audit/history` | anon | — | `{edits: [{id, ts, ts_display, type: report\|dismiss, category, name, comment, active, geometry, photo_url?}]}`, no ip/user agent/username, held reports excluded | — |
| `GET /walk-audit/photos/{filename}` | anon | — | approved photo only | 404 |

Submit resolves the nearest-road owner via `Roads.roads` and stores the report **synchronously** in `app/reports/WalkAudit` (so the app can refresh the reports layer and see the new pin), then emails staff off-thread (the email says when a report is held; photos are never attached — it links the moderation console instead). **Reports are never forwarded to municipal offices**; owner resolution is stored for analysis and the app's contact card. SMTP + recipient come from Meerschaum config `plugins:walk-audit:{smtp,notify}` — locally via `mrsm-compose.yaml` (interpolated from `.env`, repo is public), in prod from the API container's `/meerschaum/config/plugins.json`. Rate limiters are in-process and reset on restart; a reverse-proxy or shared store is still needed for multi-worker enforcement. Any signed-in user may dismiss any (non-held) report — there is no own-only rule like community rollback.

## Moderation model (release 1.22)

**Who moderates:** Meerschaum users of type `admin` on the **API instance** (the connector `/login` and `/dash` sessions use). App accounts are created as `type='user'` with username == email, so in-app admin powers (`is_admin`, "Remove" on any contribution, the pending badge) need that email-named user promoted after its first sign-in (e.g. `mrsm edit users <email>` on the API instance, or the web admin UI). The Dash console accepts any admin web session, email-named or not.

| Field | Values | Where | Meaning |
|---|---|---|---|
| `status` (text) | `published` \| `held` \| `rejected` | community (`null` = published on contributions), walk-audit + bike-parking (`published` written explicitly; `null` on old rows = published) | `held` = tripped `moderation_check`: invisible on the map, history and routing until approved. `rejected` = hidden for good |
| `photo_status` | `pending` \| `approved` \| `rejected` | community, walk-audit, bike-parking | Every new photo starts `pending`; a photo row with a null status (pre-1.22) is treated as `pending`. Photo filenames/URLs are exposed (layer GeoJSON, history, `/…/photos/{name}`) **only when `approved`** |

- **Community decisions** are appended `moderate` revisions (`moderate_contributions()`, used by both `/map-layers/community/moderate` and the console) applied in order over the original row: `status='published'` clears a hold, `'rejected'` hides it. Held text can't be re-held via the API. **Removal** is an admin rollback of the whole `replaces` chain (console requires a reason).
- **Walk-audit / bike-parking photo decisions** are written in place on the row (`set_photo_status()` re-syncs `ts`+`id` with the new `photo_status`). There is **no approve path for held walk-audit or bike-parking text** yet — it stays hidden; `pending-count.held` counts community only.
- Routing ignores held rows: `_active_community` drops held/rejected, and `_open_report_points` filters `WalkAudit.reports` with `status IS DISTINCT FROM 'held'` (see the ALTER note under tables).

**Console `/dash/moderation`** (`@web_page('moderation', login_required=True)`, admin re-checked on every callback). Shared filters: category, status (published / held / rejected / removed / all), geometry, Eastern date range, free-text `q`. Tabs:

| Tab | Shows | Actions |
|---|---|---|
| Photos | pending queue across community + walk-audit + bike-parking (ids `<source>:<row id>` for the latter two), thumbnails via 1 h HMAC-signed URLs | Approve / Reject |
| Contributions | ≤ 300 rows: date, category, name/comment, status (incl. `removed`, `replaced`), photo status, up/down, masked submitter | Approve (held), Reject (held/published), Remove (published; needs the reason box) |
| Users | one row per signed-in submitter: total / live / held / removed+rejected / latest (ignores filters) | Ban / Unban (`attributes.bwg.banned`; admins can't be banned) |
| Export | count matching the filters | Download GPX, OSM XML, GeoJSON, CSV |

**Exports** (console download or `GET /bwg/moderation/export.{fmt}`; files for JOSM / road owners — **never** calls the OSM API):

| Format | Content |
|---|---|
| GPX 1.1 | points → `wpt` (name, desc = comment, type = category, time); lines and polygon outlines → `trk` |
| OSM XML `.osm` | `version=0.6`, `upload="false"`, new objects with negative ids, nodes before ways; polygons as closed ways |
| GeoJSON | FeatureCollection; props `id, created, category, name, comment, status, upvotes, downvotes, photo_url` (absolute, approved only), `source` |
| CSV | `id, created, category, name, comment, status, upvotes, downvotes, geometry_type, lat, lon, length_m, photo_url, wkt` |

OSM tag mapping (`osm_tags()`), every object also gets `source=Bike Walk Greenville community app`, `bwg:category`, `bwg:id`:

| Contribution | Tags |
|---|---|
| `route-suggestion` / `shortcut` LineString | `highway=path`, `bicycle=yes`, `name` (if any), `note` = comment |
| `no-entry` Polygon | `access=no`, `note` = "name - comment" |
| `bike-parking` point | `amenity=bicycle_parking` + note |
| `repair-station` point | `amenity=bicycle_repair_station` + note |
| `water-fountain` point | `amenity=drinking_water` + note |
| anything else | `note` only (no feature tag) |

## Abuse guards added late in 1.22 (security review)

| Guard | Where | Behaviour |
|---|---|---|
| Token provenance | `bwg-auth` `bwg_user` | Only tokens bwg-auth minted (a `kind='token'` row in `BwgAuth.login_codes`) are accepted for non-admin users; others → 401. Admin users' tokens are always accepted. |
| Email characters | `request-code` | `--`, `;`, quotes, backticks, whitespace → 400 (Meerschaum `build_where` would drop the WHERE clause). |
| Code guessing | `verify` | 5 wrong guesses per code AND 20 per email per 24 h → 429 (even for the right code). |
| Per-user limits | submit-point, rollback, walk-audit submit, bike-parking submit, feedback | 10 writes/hour per **username** (was per IP). Votes/confirm have their own 120/hour budget. |
| Image sniffing | every photo upload | Magic bytes decide JPEG/PNG/WebP (HEIC dropped; iOS image_picker delivers JPEG); stored extension follows the sniff; 8 MB cap; served with `X-Content-Type-Options: nosniff`. |
| Group rides | `/nearby` 120/h per IP, `/join` 30/h per IP, 60 members per ride (409), 16 KB bodies (413; route 200 KB), `distance_m` rounded to 50 m, rides ended > 7 days purged hourly. |
| Held text everywhere | walk-audit / bike-parking | `set_status(ids, status)`; console "Held reports" table covers all three sources; `pending-count.held` sums them; `moderate` accepts `status='held'`; rejected reports leave `reports.geojson`/`history`. |
| Dismiss ownership | `walk-audit/dismiss` | 403 unless admin or the report's `username` matches (legacy null username → admin only). |
| Photo-link key | moderation console | HMAC key = `meerschaum.api._oauth2.SECRET` (random, file-backed), not the connector URI. |
| Repeat-text filter | `moderation_check(text, username=None)` | Counted per `(username, text)`; skipped when no username (group-ride names). |

Prod config the guards assume (set at the 1.22 deploy): `api:permissions:registration:users: false` (else anyone can register an email-named user and mint a `bwg` token) and `api:uvicorn:forwarded_allow_ips` = the nginx hop (`172.19.0.1`, the docker gateway) because nginx appends `$proxy_add_x_forwarded_for`; with `'*'` every per-IP limit is spoofable.

Group-ride route revisions (late 1.22): rides carry `route_rev` (0 at create, +1 per `PUT /route`). `/ping` always returns `route_rev` and includes `route` only when the body's `route_rev` is absent or stale. `PUT /route {route: null}` clears the leader route and returns `{ok, route_rev}`. The app never sends `Authorization` to `/group-rides/*`.

## App-write tables (sql:bwg)

All are Meerschaum pipes declared in the plugins with explicit `dtypes`. **Tables and new columns are created by Meerschaum on the first sync that carries them** — no migration step; readers backfill missing columns (`SELECT *` + defaults in map-layers, `select_columns` filtered to existing columns in walk-audit / bike-parking). Emails in `username` columns are private and never served.

| Table | Pipe keys | Index | Columns |
|---|---|---|---|
| `BwgAuth.login_codes` | `app` / `login_codes` / `BwgAuth` | dt `ts`, id `id` | append-only: `kind` (code \| fail \| used), `email`, `code_id`, `code_hash` (sha256 of `email:code`), `expires`, `ip`. Never served |
| `BwgApp.saved_routes` | `app` / `saved_routes` / `BwgApp` | dt `created`, id `id` | `username, name, from_lat, from_lon, to_lat, to_lon, modes, stress, distance_m, duration_min, geometry` (GeoJSON LineString text); ≤ 200 per user; DELETE clears the row |
| `BwgApp.events` | `plugin:bwg-events` / `events` | dt `start`, id `uid` | `end, all_day, title, location, description, html_link, luma_url, synced_at`; recurrences expanded −1 / +90 days; uid of an occurrence = `<uid>-<YYYYmmddTHHMMSSZ>` |
| `GroupRides.rides` | `app` / `group_rides` / `rides` | primary `id`, upsert | `code, name, leader_member_id, route` (Feature JSON), `created, ended, last_leader_ping` |
| `GroupRides.members` | `app` / `group_rides` / `members` | primary `id`, upsert | `ride_id, name, token_hash` (sha256; null = left), `lat, lon, heading, speed, updated, is_leader` |
| `MapLayers.community_revisions` | `app` / `community_revisions` / `MapLayers` | dt `ts`, id `id` | existing `category, name, comment, geometry_json, reverts, replaces, photo_filename, lat, lon, confirms, voter` + **new** `username` (author / voter / moderator), `vote` (up \| down \| null), `status` (held \| rejected \| null), `photo_status` (pending \| approved \| rejected). `voter` kept for legacy `confirm` rows |
| `MapLayers.layer_feedback` | `app` / `feedback` / `MapLayers` | dt `ts`, id `id` | + **new** `username` |
| `MapLayers.point_submissions` | `app` / `point_submissions` / `MapLayers` | dt `ts`, id `id` | legacy private submissions; no longer written |
| `WalkAudit.reports` | `app` / `reports` / `WalkAudit` | dt `ts`, id `id` | + **new** `status` (published \| held), `photo_status`, `username` |
| `WalkAudit.report_edits` | `app` / `report_edits` / `WalkAudit` | dt `ts`, id `id` | + **new** `username` |
| `BikeParking.parking_feedback` | `app` / `feedback` / `BikeParking` | dt `ts`, id `id` | + **new** `status` (published \| held), `photo_status`, `username` |

**One manual ALTER.** `_open_report_points` (map-layers) reads `WalkAudit.reports` with raw SQL and tries `… AND r."status" IS DISTINCT FROM 'held'` first, silently falling back to the unfiltered query if that fails. Until the first walk-audit 0.4.0 submission creates the column the fallback is harmless (no held rows can exist), but to guarantee the filter is always the one that runs, add the columns once in prod:

```sql
ALTER TABLE "WalkAudit"."reports"
  ADD COLUMN IF NOT EXISTS "status" TEXT,
  ADD COLUMN IF NOT EXISTS "photo_status" TEXT,
  ADD COLUMN IF NOT EXISTS "username" TEXT;
```

## Gaps / dead weight

1. **GTFS schedules**: routes/stops/shapes are ingested (`projects/transit.yaml`), `stop_times` are not — transit waits are a flat estimate.
2. **Lane counts**: no column; PCC stress already encodes the derived risk; Replica/OSM would be sources if needed.
3. `Sidewalks.county_sidewalks` empty; `projects/gcgis.yaml` has an empty-parameter stub pipe; `plugins/app.py` dead Flet prototype.
4. City sidewalks are polygons (heavy) — prefer `county.sidewalks` lines + `streets_with_sidewalks` for rendering.
5. **Abuse guards**: `POST /map-layers/feedback` has no rate limit or photo size cap and `POST /bike-parking/submit` has no rate limit; every limiter is in-process (per worker, reset on restart).
6. `projects/scdps-incidents.yaml` targets `sql:traffic`→`sql:main`, legacy schema — not part of the `sql:bwg` graph.

## Mobile app (app-native/)

Native Flutter (replaces Flet `bwg_app/`). MapLibre GL consumes the GeoJSON endpoints above directly. Signing: `app-native/keystore.properties` + `sra-upload.keystore` (gitignored; backup in Google Drive → shared SRA upload key, SHA1 `537F9A88AAB6623CFA91F0FBABCE6F95E705A843`). Conventions copied from `~/projects/trail-counter/app-native` (flat `lib/`, provider + single AppState, Dio single-chokepoint client, version = pubspec `x.y.z+N`, `gplay` CLI for Play).

The app's Dio client sends `Authorization: Bearer <token>` whenever signed in (token in `flutter_secure_storage`); any 401 signs the user out and re-prompts once. When a write returns `status: 'held'` the app says the submission is awaiting review.

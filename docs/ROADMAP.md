# BWG App + Data Roadmap (June 2026 – May 2027)

Board-requested map features for the BikeWalk Greenville app (`bwg_app/`), backed by the
Meerschaum data pipeline in this repo. Deadline: **May 2027**.

## Goals

1. **Walk Audit** — users submit accessibility issues from the map (missing/narrow sidewalk,
   street needs bike lane, feels unsafe, maintenance issues routed via "Who Owns the Roads?").
2. **Bus routes** map layer.
3. **Bike stress** on streets (Palmetto Cycling Coalition / Palmetto Walk Bike stress table).
4. **Bike lanes** map layer.
5. **Bike repair stations** map pins.
6. **Sidewalks** map layer (city + county sources).
7. **Mobility-Friendly Business** icon pins (e.g., Swamp Rabbit Cafe).

## Architecture

```
Meerschaum pipes (sql:bwg, Postgres/PostGIS)
        │  nightly `mrsm export map layers` (simplify + reproject → WGS84)
        ▼
plugins/map-layers.py  ──►  GET /map-layers/index.json      (layer catalog)
                            GET /map-layers/<layer>.geojson (pregenerated or on-demand)
        │
        ▼
bwg_app  "Greenville Mobility Map"  (flet_map)
  - polyline layers (routes, lanes, sidewalks, stress) w/ per-layer or per-feature color
  - marker layers (stops, repair stations, businesses, bike parking)
  - layer toggle bottom sheet, lazy loading
  - report dialogs POST back to plugin endpoints (bike-parking today, walk-audit in Phase 2)
```

The app discovers layers from `index.json`, so new layers ship without an APK release.
User submissions follow the established `plugins/bike-parking.py` pattern
(`@api_plugin` POST endpoint → photo to `<root>/uploads/...` → autotime feedback pipe).

## Feature matrix

| Feature | Data status | Pipeline work | App work | Milestone |
|---|---|---|---|---|
| Bus routes + stops | **Exists** — `city."BusRoutes"` (LineString 6570), `city."BusStops"` (Point 6570), `projects/city.yaml` | Register in `map-layers.py` | Polyline layer + stop pins | Aug 2026 |
| Bike lanes | **Exists** — `city."BicycleInfrastructure"` (`bike_type`, `street_name`, 3361) | Register layer, keep `bike_type` prop | Polyline layer, color by `bike_type` | Aug 2026 |
| Sidewalks | **Exists** — `city."Sidewalks"` (3361) + `"Sidewalks".county_sidewalks` (6570, `projects/sidewalks.yaml`) | Two layers, aggressive simplification (largest payload) | City/county toggleable polyline layers | Aug 2026 |
| Bike stress (PCC) | **Exists** — `pcc.stress_levels` w/ `stress_level` H/MH/M/ML/L (`projects/pcc.yaml`) | pgRouting topology + low-stress route endpoint | Route planner UI drawing the returned polyline (NOT a display layer — full network too dense for on-device rendering) | Feb 2027 |
| Swamp Rabbit Trail | **Exists** — `"SRT".segments_owners` (Owner/Segment, 4326, `projects/srt.yaml`) | Registered in `map-layers.py` | Polyline layer, on by default | **Done (June 2026)** |
| Walk Audit | **Missing** — pattern in `plugins/bike-parking.py`; jurisdiction in `"Roads".roads` | `plugins/walk-audit.py`: categories, moderated submissions pipe, nearest-road owner lookup | Tap-to-place pin, category dropdown, photo, post-submit owner contact card | Nov 2026 |
| Bike repair stations | **Missing** | OSM Overpass `amenity=bicycle_repair_station` + manual CSV → `app/locations/RepairStations`; `projects/repair-stations.yaml` | Wrench-icon marker layer | Feb 2027 |
| Mobility-Friendly Businesses | **Missing** | Board-curated CSV/Sheet → `app/locations/MobilityBusinesses`; optional nominate endpoint; `projects/mobility-businesses.yaml` | Storefront icon pins + detail panel w/ website link | Feb 2027 |

## Phases

### Phase 0 — Shared layer infrastructure (June 2026)

- `plugins/map-layers.py`: `LAYERS` registry (pipe keys, kept properties, kind, simplify
  tolerance), `GET /map-layers/index.json`, `GET /map-layers/<layer>.geojson`,
  `mrsm export map layers` pregeneration action (writes
  `<data>/output/geojson/app/<layer>.geojson`, served like `geojson-export.py` output).
- App: generalize geojson parsing (Point/LineString/MultiLineString), `fm.PolylineLayer`
  rendering, per-feature color resolution, layer toggle bottom sheet, lazy load + cache.
- Internal release **0.2.0**.

### Phase 1 — Quick-win layers (July – mid-August 2026)

- Enable bus routes/stops, bike lanes, sidewalks (city + county), and the Swamp Rabbit
  Trail on a single "Greenville Mobility Map" tool. (Bike stress dropped as a display
  layer — see low-stress routing, Phase 3.)
- Tune payload sizes + polyline performance on real devices.
- Tester releases 0.2.x → public **0.3.0** (~Aug 2026).

### Phase 2 — Walk Audit (September – November 2026)

Flagship board feature; longest runway.

- `plugins/walk-audit.py` (clone bike-parking skeleton):
  - Categories: `missing_sidewalk`, `narrow_sidewalk`, `needs_bike_lane`,
    `unsafe_crossing`, `feels_unsafe`, `maintenance`, `other`.
  - Pipe `app/feedback/WalkAudit` → `"WalkAudit".audit_reports`, autotime, with
    `status` column (default `new`) — **moderated**: nothing public until reviewed.
  - `POST /walk-audit/submit` (multipart, photo size-capped, IP rate-limited),
    `GET /walk-audit/categories.json`, `GET /walk-audit/data.geojson` (reviewed only).
  - Nearest-road jurisdiction lookup against `"Roads".roads` (parameterized SQL,
    `ST_DWithin` + KNN `<->`); response includes owner name/phone/email/online-form so
    maintenance issues route to the right agency ("Who Owns the Roads?" tie-in).
- App: tap-on-map pin placement, category dropdown, description + photo, post-submit
  contact card with `tel:`/`mailto:` links.
- Moderation: Grafana table panel over `"WalkAudit".audit_reports` + status updates.
- Release **0.4.0** (Nov 2026).

### Phase 3 — New datasets + low-stress routing (December 2026 – February 2027)

- **Low-stress routing (PCC stress data)**: the stress network is routing input,
  not a display layer (28k segments overwhelm on-device polyline rendering).
  Google Maps **cannot** consume custom road weights — its Directions API only
  offers its own `bicycling` mode, which ignores PCC stress. So routing is
  self-hosted:
  - Enable `pgrouting` in the stack's Postgres (or a sidecar); build a topology
    over `pcc.stress_levels` (`pgr_createTopology`), edge cost =
    length × stress multiplier (e.g. L=1, ML=1.2, M=1.8, MH=4, H=10).
  - `GET /routes/low-stress?from=lat,lon&to=lat,lon` in a new plugin →
    `pgr_dijkstra` → route polyline + total distance/stress summary.
  - App: origin/destination picker (tap or GPS), draw the returned polyline,
    "Open in Google Maps" deep-link only as a turn-by-turn handoff along our
    waypoints (Google re-routes between waypoints its own way — lossy).
- **Repair stations**: Overpass query (Greenville County bbox) + manual CSV for
  BWG-installed stations; `projects/repair-stations.yaml`, weekly sync.
- **Mobility-Friendly Businesses**: curated CSV/Google Sheet (name, category, lat/lon,
  address, website, accessibility notes); optional `POST /mobility-businesses/nominate`.
  Requires a board curation process — start collecting the list early.
- Walk-audit category addition for damaged repair stations.
- Release **0.5.0** (Feb 2027).

### Phase 4 — Hardening & 1.0 (March – May 2027)

Deliberately light so Phase 2/3 slips absorb here.

- Marker clustering, on-device geojson caching / offline tolerance.
- Optional: swap bus layer to Greenlink GTFS if city GIS proves stale.
- Accessibility pass, crash review, Play Store listing polish.
- Release **1.0.0** by May 2027.

## Release cadence

Every APK release MUST bump both (see CLAUDE.md):

- `[project] version` in `bwg_app/pyproject.toml` (→ `versionName`)
- `--build-number` (→ `versionCode`, strictly increasing — Android silently keeps the old
  install otherwise)

```bash
cd bwg_app && uv run flet build apk --build-number <N+1> --build-version <x.y.z>
```

## Risks

1. **Payload size / polyline performance** — county sidewalks + full stress network are
   large. Mitigate: simplify in native ft units before reprojection, prune properties,
   gzip (~10:1), `ST_LineMerge` contiguous segments server-side, per-area splits if
   needed. Test on a low-end Android early in Phase 1.
2. **Walk-audit spam/abuse** — public unauthenticated POST. Mitigate: moderation gate,
   IP rate limit, photo size cap.
3. **SRID/unit mistakes** — 6570 and 3361 are both ft-based SC state plane variants;
   wrong tolerance units silently corrupt output. Verify one known segment per layer.
4. **Bus data currency** — city GIS `BusRoutes` may lag Greenlink; GTFS swap scoped as
   optional Phase 4 work so it never blocks Phase 1.
5. **flet 0.80.x pinning** — all extensions must match `flet` version exactly; any flet
   upgrade is its own task with device testing.

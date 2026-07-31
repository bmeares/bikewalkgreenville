#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Map Layers: serve infrastructure layers (bus routes, bike lanes, sidewalks,
PCC bike stress, ...) as mobile-friendly GeoJSON for the BWG app.

Routes (mounted on the Meerschaum API FastAPI app, i.e. https://bwg.mrsm.io):

  GET /map-layers/index.json        -> catalog of available layers + style hints
  GET /map-layers/{layer}.geojson   -> FeatureCollection (pregenerated file if
                                       present, else generated on demand)

Pregeneration (run nightly after the source pipes sync):

  mrsm compose exec export map_layers

writes `<data>/output/geojson/app/<layer>.geojson` alongside the existing
`export geojson` output tree.

Geometries are simplified in their native SC state-plane CRS (ft units) before
reprojection to WGS84 to keep payloads small enough for flet_map on-device.
"""

import math
import threading
import time
from typing import Any

import meerschaum as mrsm
from meerschaum.actions import make_action
from meerschaum.plugins import api_plugin
from meerschaum.utils.warnings import info, warn

__version__ = '0.2.0'

bwg = mrsm.Plugin('bwg')

FT_PER_M = 3.28084

#: Single source of truth for app map layers.
#: `pipe`: (connector, metric[, location]) on instance sql:bwg.
#: `props`: output property name -> DB column name.
#: `tolerance_m`: Douglas-Peucker tolerance in meters (lines only; applied in
#:   the native ft-based CRS before reprojection).
LAYERS: dict[str, dict[str, Any]] = {
    'bus-routes': {
        'label': 'Bus Routes',
        'kind': 'line',
        'pipe': ('plugin:gtfs', 'route_shapes', 'greenlink'),
        'props': {'route': 'short_name', 'name': 'long_name', 'color': 'color'},
        'tolerance_m': 5,
        'color': '#7B1FA2',
    },
    'bus-stops': {
        'label': 'Bus Stops',
        'kind': 'point',
        'pipe': ('plugin:gtfs', 'stops', 'greenlink'),
        'props': {'name': 'name', 'routes': 'routes'},
        'color': '#7B1FA2',
        'icon': 'directions_bus',
    },
    'bike-lanes': {
        'label': 'Bike Lanes',
        'kind': 'line',
        'pipe': ('plugin:city-gis', 'BicycleInfrastructure'),
        'props': {'bike_type': 'BIKE_TYPE', 'street_name': 'STREET_NAM'},
        'tolerance_m': 5,
        'color': '#2E7D32',
    },
    'sidewalks-city': {
        'label': 'Sidewalks (City)',
        'kind': 'line',
        'pipe': ('plugin:city-gis', 'Sidewalks'),
        'props': {},
        'detail_props': {
            'material': 'MATERIAL',
            'side_of_street': 'SIDE_OF_ST',
            'street_name': 'STREET_NAM',
        },
        'tolerance_m': 8,
        'dissolve_by': True,
        'color': '#1565C0',
    },
    'sidewalks-county': {
        'label': 'Sidewalks (County)',
        'kind': 'line',
        'pipe': ('plugin:greenville-county', 'sidewalks'),
        'props': {},
        'tolerance_m': 8,
        'dissolve_by': True,
        'color': '#0288D1',
    },
    'srt': {
        'label': 'Swamp Rabbit Trail',
        'kind': 'line',
        'pipe': ('sql:bwg', 'srt_segments', 'owners'),
        'props': {'owner': 'Owner', 'segment': 'Segment'},
        'tolerance_m': 3,
        'color': '#FF6F00',
    },
    'bike-stress': {
        'label': 'Bike Stress',
        'kind': 'line',
        'pipe': ('sql:bwg', 'stress_levels', 'greenville'),
        'props': {'stress_level': 'stress_level'},
        'detail_props': {'street_name': 'street_name'},
        'tolerance_m': 30,
        'min_part_length_m': 300,
        'dissolve_by': 'stress_level',
        'color_by': {
            'property': 'stress_level',
            'map': {
                'H': '#d73027',
                'MH': '#fc8d59',
                'M': '#fee08b',
                'ML': '#91cf60',
                'L': '#1a9850',
            },
        },
    },
}

#: On-demand generation cache: layer id -> (epoch, geojson string).
_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL_SECONDS = 6 * 60 * 60


def _layer_pipe(layer: dict[str, Any]) -> mrsm.Pipe:
    keys = layer['pipe']
    return mrsm.Pipe(*keys, instance='sql:bwg')


def _output_dir():
    """Pregenerated layer files live with the `export geojson` output tree.
    Falls back to the Meerschaum root (e.g. inside the API container, where the
    `bwg` plugin and its data_path config aren't installed)."""
    if bwg.module is not None:
        return bwg.module.get_data_path() / 'output' / 'geojson' / 'app'
    from pathlib import Path
    from meerschaum.config.paths import ROOT_DIR_PATH
    return Path(ROOT_DIR_PATH) / 'output' / 'geojson' / 'app'


def _build_layer_geojson(layer_id: str, debug: bool = False) -> str | None:
    """Read the layer's pipe, simplify + reproject, return a GeoJSON string."""
    layer = LAYERS[layer_id]
    pipe = _layer_pipe(layer)
    if not pipe.exists(debug=debug):
        warn(f"Pipe for layer '{layer_id}' does not exist: {pipe}")
        return None

    geometry_cols = [
        col
        for col, typ in pipe.dtypes.items()
        if 'geometry' in typ.lower() or 'geography' in typ.lower()
    ]
    if not geometry_cols:
        warn(f"No geometry column for layer '{layer_id}' ({pipe}).")
        return None
    geom_col = geometry_cols[0]

    props = layer.get('props', {})
    df = pipe.get_data([geom_col] + list(props.values()), debug=debug)
    if df is None or len(df) == 0:
        warn(f"No data for layer '{layer_id}' ({pipe}).")
        return None

    df = df.rename(columns={db_col: out_name for out_name, db_col in props.items()})

    try:
        df.geometry = df.geometry.force_2d()  # Z coords (e.g. SRT) waste bytes
    except Exception:
        pass

    # Merge contiguous segments (huge feature-count + payload reduction for
    # sidewalks/stress, which arrive as thousands of tiny per-block segments).
    dissolve_by = layer.get('dissolve_by')
    if dissolve_by:
        df = (
            df.dissolve(by=dissolve_by, as_index=False)
            if dissolve_by is not True
            else df.dissolve()
        )
        df.geometry = df.geometry.line_merge()

    tolerance_m = layer.get('tolerance_m')
    crs_is_ft = df.crs is not None and df.crs.is_projected
    if tolerance_m and layer.get('kind') == 'line' and crs_is_ft:
        df.geometry = df.geometry.simplify(
            tolerance_m * FT_PER_M,
            preserve_topology=True,
        )

    # Dissolved Multi* features hide thousands of tiny disconnected slivers;
    # exploding + dropping the short tail cuts the coord count dramatically
    # without visibly changing the overview.
    min_part_length_m = layer.get('min_part_length_m')
    if min_part_length_m and crs_is_ft:
        df = df.explode(index_parts=False)
        df = df[df.geometry.length >= min_part_length_m * FT_PER_M]

    df = df.to_crs(4326)
    try:
        # ~1.1 m grid: 5-decimal coords serialize ~40% smaller.
        df.geometry = df.geometry.set_precision(1e-5)
    except Exception:
        pass
    return df.to_json(drop_id=True)


#: Cached native SRID per layer (one `ST_SRID` query each).
_SRIDS: dict[str, int] = {}


def _layer_srid(layer_id: str, conn, schema: str, target: str) -> int:
    if layer_id not in _SRIDS:
        df = conn.read(f'SELECT ST_SRID("geometry") AS "srid" FROM "{schema}"."{target}" LIMIT 1')
        _SRIDS[layer_id] = int(df['srid'][0])
    return _SRIDS[layer_id]


def _build_bbox_geojson(
    layer_id: str,
    minlon: float,
    minlat: float,
    maxlon: float,
    maxlat: float,
    zoom: int,
) -> str:
    """Viewport query: bbox-filtered, zoom-simplified features WITH per-feature
    detail properties (the full-layer path dissolves those away). Pure PostGIS —
    no geopandas round-trip. All numeric inputs are coerced before
    interpolation; `layer_id` is validated against LAYERS by the caller.
    """
    import json
    import math

    layer = LAYERS[layer_id]
    pipe = _layer_pipe(layer)
    conn = pipe.instance_connector
    schema = pipe.parameters.get('schema') or 'public'
    target = pipe.target

    minlon, minlat, maxlon, maxlat = (
        float(minlon), float(minlat), float(maxlon), float(maxlat),
    )
    zoom = max(1, min(int(zoom), 22))

    props = {**layer.get('props', {}), **layer.get('detail_props', {})}
    prop_cols = ''.join(
        f', "{db_col}" AS "{out_name}"'
        for out_name, db_col in props.items()
    )

    # ~1 px worth of simplification at this zoom (Greenville latitude), in the
    # layer's native units (US-ft state plane, or degrees for 4326 layers).
    m_per_px = 156543.03 * math.cos(math.radians(34.85)) / (2 ** zoom)
    srid = _layer_srid(layer_id, conn, schema, target)
    if srid == 4326:
        tolerance = m_per_px / 111320
    else:
        tolerance = max(m_per_px * FT_PER_M, 1.0)

    envelope = (
        f"ST_Transform(ST_MakeEnvelope({minlon}, {minlat}, {maxlon}, {maxlat}, 4326), {srid})"
    )
    bbox_limit = int(layer.get('bbox_limit', 4000))
    query = f'''
    SELECT
        ST_AsGeoJSON(
            ST_Force2D(
                ST_Transform(ST_SimplifyPreserveTopology("geometry", {tolerance}), 4326)
            ),
            5
        ) AS "gj"
        {prop_cols}
    FROM "{schema}"."{target}"
    WHERE ST_Intersects("geometry", {envelope})
    ORDER BY ST_Length("geometry") DESC
    LIMIT {bbox_limit}
    '''
    def _clean(v):
        if hasattr(v, 'item'):
            v = v.item()
        if isinstance(v, float) and v != v:  # NaN -> null
            return None
        return v

    df = conn.read(query)
    features = []
    for row in df.to_dict(orient='records'):
        gj = row.pop('gj', None)
        if not gj:
            continue
        features.append({
            'type': 'Feature',
            'geometry': json.loads(gj),
            'properties': {k: _clean(v) for k, v in row.items()},
        })
    return json.dumps({'type': 'FeatureCollection', 'features': features})


#: Greenville County-ish bounds for search (minlon, minlat, maxlon, maxlat).
SEARCH_BOUNDS = (-82.65, 34.58, -82.10, 35.10)

#: Nominatim fallback cache: query -> (epoch, results).
_NOMINATIM_CACHE: dict[str, tuple[float, list]] = {}
_NOMINATIM_TTL_SECONDS = 15 * 60


def _escape_like(q: str) -> str:
    return q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _search_local(q: str, limit: int) -> list[dict[str, Any]]:
    """One UNION ALL round trip over bike parking, bus stops, city addresses,
    and PCC street names. ILIKE is fine at these row counts (<= 50k)."""
    conn = mrsm.get_connector('sql:bwg')
    params = {'q': f'%{_escape_like(q)}%', 'qp': f'{_escape_like(q)}%'}

    arms = []
    parking_pipe = mrsm.Pipe('app', 'locations', 'BikeParking', instance='sql:bwg')
    if parking_pipe.exists():
        arms.append(r'''
        (SELECT
            "name" AS "label",
            COALESCE(NULLIF("address", ''), 'Bike parking') AS "sublabel",
            "lat", "lon",
            'bike-parking' AS "kind",
            ("name" ILIKE :qp ESCAPE '\') AS "prefix"
        FROM "BikeParking"."parking_locations"
        WHERE "name" ILIKE :q ESCAPE '\' OR "address" ILIKE :q ESCAPE '\'
        LIMIT 5)
        ''')
    arms.append(r'''
    (SELECT
        "name" AS "label",
        COALESCE(NULLIF('Bus stop — routes ' || NULLIF("routes", ''), 'Bus stop — routes '), 'Bus stop') AS "sublabel",
        "lat", "lon",
        'bus-stops' AS "kind",
        ("name" ILIKE :qp ESCAPE '\') AS "prefix"
    FROM "transit"."stops"
    WHERE "name" ILIKE :q ESCAPE '\'
    LIMIT 5)
    ''')
    arms.append(r'''
    (SELECT DISTINCT ON ("FULLADDRES")
        INITCAP("FULLADDRES") AS "label",
        COALESCE("ZIPCODE"::text, 'Greenville') AS "sublabel",
        ST_Y(ST_Transform("geometry", 4326)) AS "lat",
        ST_X(ST_Transform("geometry", 4326)) AS "lon",
        'address' AS "kind",
        ("FULLADDRES" ILIKE :qp ESCAPE '\') AS "prefix"
    FROM "city"."Addresses"
    WHERE "FULLADDRES" ILIKE :q ESCAPE '\'
    ORDER BY "FULLADDRES"
    LIMIT 5)
    ''')
    arms.append(r'''
    (SELECT
        INITCAP("street_name") AS "label",
        'Street' AS "sublabel",
        ST_Y(ST_Transform(ST_Centroid(ST_Collect("geometry")), 4326)) AS "lat",
        ST_X(ST_Transform(ST_Centroid(ST_Collect("geometry")), 4326)) AS "lon",
        'street' AS "kind",
        BOOL_OR("street_name" ILIKE :qp ESCAPE '\') AS "prefix"
    FROM "pcc"."stress_levels"
    WHERE "street_name" ILIKE :q ESCAPE '\'
    GROUP BY "street_name"
    LIMIT 5)
    ''')

    query = f'''
    SELECT "label", "sublabel", "lat", "lon", "kind"
    FROM ({' UNION ALL '.join(arms)}) AS "matches"
    ORDER BY "prefix" DESC, LENGTH("label")
    LIMIT {int(limit)}
    '''
    df = conn.read(query, params=params)
    results = []
    for row in df.to_dict(orient='records'):
        lat, lon = row.get('lat'), row.get('lon')
        if lat is None or lon is None or lat != lat or lon != lon:
            continue
        results.append({
            'label': row.get('label') or '',
            'sublabel': row.get('sublabel') or '',
            'lat': float(lat),
            'lon': float(lon),
            'kind': row.get('kind') or '',
        })
    return results


def _search_nominatim(q: str) -> list[dict[str, Any]]:
    """Geocoder fallback for addresses outside the city datasets. Server-side
    so the User-Agent/caching requirements of the Nominatim usage policy live
    in one place."""
    import requests

    cached = _NOMINATIM_CACHE.get(q.lower())
    if cached and (time.time() - cached[0]) < _NOMINATIM_TTL_SECONDS:
        return cached[1]

    minlon, minlat, maxlon, maxlat = SEARCH_BOUNDS
    resp = requests.get(
        'https://nominatim.openstreetmap.org/search',
        params={
            'q': q,
            'format': 'jsonv2',
            'limit': 6,
            'viewbox': f'{minlon},{maxlat},{maxlon},{minlat}',
            'bounded': 1,
        },
        headers={'User-Agent': f'bwg-map-layers/{__version__} (data@bikewalkgreenville.org)'},
        timeout=5,
    )
    resp.raise_for_status()
    results = []
    for item in resp.json():
        name = item.get('display_name') or ''
        parts = [p.strip() for p in name.split(',')]
        results.append({
            'label': parts[0] if parts else name,
            'sublabel': ', '.join(parts[1:3]),
            'lat': float(item['lat']),
            'lon': float(item['lon']),
            'kind': 'osm',
        })
    _NOMINATIM_CACHE[q.lower()] = (time.time(), results)
    return results


# =========================================================================
# Routing (bike / walk / transit)
#
# Feasible-path finder over the Swamp Rabbit Trail, bike lanes, and PCC
# streets -- shared graph, per-mode edge weights. Transit rides Greenlink
# route shapes between stops, with walking legs on either end. The graph is
# built lazily from PostGIS, cached in-process, and traversed with A*.
# =========================================================================

#: Edge weight multiplier per category and mode. Bike leans hard on stress
#: (trail < bike lane < LTS levels); walking mostly cares about distance with
#: a mild preference for the trail and calmer streets.
MODE_FACTORS = {
    'bike': {
        'srt': 0.4,
        'bike-lane': 0.4,
        'L': 1.0,
        'ML': 1.3,
        'M': 2.5,
        'MH': 6.0,
        'H': 12.0,
    },
    'walk': {
        'srt': 0.7,
        'bike-lane': 1.0,
        'L': 1.0,
        'ML': 1.0,
        'M': 1.1,
        'MH': 1.25,
        'H': 1.5,
    },
}
MODE_DEFAULT_FACTOR = {'bike': 2.5, 'walk': 1.1}
MODE_SPEED_M_S = {'bike': 4.2, 'walk': 1.35}  # casual cycling / walking pace
MODE_NETWORK_NOUN = {'bike': 'bikeable', 'walk': 'walkable'}
ROUTE_SNAP_MAX_M = 400      # reject termini farther than this from the network
ROUTE_SUBDIVIDE_M = 120.0   # split long lines so they're enterable mid-way
ROUTE_GRID_M = 12.0         # endpoint snap cell (stitches segment breaks)
ROUTE_STITCH_M = 60.0       # max connector length between components
ROUTE_CONNECT_M = 120.0     # max trail/lane -> street junction connector

# Transit tuning: how far someone will walk to/from a stop, how close a stop
# must sit to its route shape to count as "on" it, minimum useful ride, an
# average in-service bus speed, and a flat wait estimate (no stop_times yet).
TRANSIT_WALK_MAX_M = 1500.0
TRANSIT_STOP_SNAP_M = 100.0
TRANSIT_MIN_RIDE_M = 250.0
TRANSIT_BUS_SPEED_M_S = 6.5
TRANSIT_WAIT_MIN = 8.0

M_PER_DEG_LAT = 111320.0
_COSLAT = math.cos(math.radians(34.85))  # Greenville-latitude lon scale

_GRAPH: dict[str, Any] = {'epoch': 0.0, 'nodes': None, 'adj': None}
_GRAPH_LOCK = threading.Lock()
_GRAPH_TTL_SECONDS = 24 * 60 * 60


def _equirect_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dx = (lon2 - lon1) * M_PER_DEG_LAT * _COSLAT
    dy = (lat2 - lat1) * M_PER_DEG_LAT
    return (dx * dx + dy * dy) ** 0.5


def _route_source_rows(debug: bool = False) -> list[tuple[list, str, str]]:
    """Pull (coords, category, street name) rows for every routable segment.
    Geography-cast lengths sidestep per-CRS units; simplification preserves
    endpoints, so connectivity is unaffected. The name feeds turn-by-turn
    instructions ("Turn right onto Main St")."""
    import json

    sources = [
        # (layer, category, SQL expression for the street name)
        ('bike-stress', None, '"street_name"'),   # category = stress_level per row
        ('bike-lanes', 'bike-lane', '"STREET_NAM"'),
        ('srt', 'srt', "'Swamp Rabbit Trail'"),
    ]
    rows = []
    for layer_id, category, name_expr in sources:
        layer = LAYERS[layer_id]
        pipe = _layer_pipe(layer)
        conn = pipe.instance_connector
        schema = pipe.parameters.get('schema') or 'public'
        target = pipe.target
        srid = _layer_srid(layer_id, conn, schema, target)
        tolerance = (5 / M_PER_DEG_LAT) if srid == 4326 else (5 * FT_PER_M)
        cat_col = (
            '"stress_level" AS "category"'
            if layer_id == 'bike-stress'
            else f"'{category}' AS \"category\""
        )
        query = f'''
        SELECT
            ST_AsGeoJSON(
                ST_Force2D(
                    ST_Transform(
                        ST_SimplifyPreserveTopology("geometry", {tolerance}),
                        4326
                    )
                ),
                5
            ) AS "gj",
            {cat_col},
            {name_expr} AS "name"
        FROM "{schema}"."{target}"
        '''
        df = conn.read(query, debug=debug)
        for rec in df.to_dict(orient='records'):
            gj = rec.get('gj')
            if not gj:
                continue
            geom = json.loads(gj)
            parts = (
                geom['coordinates']
                if geom['type'] == 'MultiLineString'
                else [geom['coordinates']]
            )
            name = rec.get('name')
            name = None if not name or str(name).strip() in ('', 'N/A', 'None') else str(name).strip()
            for part in parts:
                if len(part) >= 2:
                    rows.append((part, rec.get('category'), name))
    return rows


def _grid_node(lat: float, lon: float) -> tuple[int, int]:
    cell_lat = ROUTE_GRID_M / M_PER_DEG_LAT
    cell_lon = cell_lat / _COSLAT
    return (round(lat / cell_lat), round(lon / cell_lon))


def _subdivide(coords: list) -> list[list]:
    """Split a [lon, lat] coord list into chunks of ~ROUTE_SUBDIVIDE_M so long
    features (the SRT especially) can be entered/exited mid-way."""
    chunks = []
    chunk = [coords[0]]
    acc = 0.0
    for a, b in zip(coords, coords[1:]):
        acc += _equirect_m(a[1], a[0], b[1], b[0])
        chunk.append(b)
        if acc >= ROUTE_SUBDIVIDE_M:
            chunks.append(chunk)
            chunk = [b]
            acc = 0.0
    if len(chunk) >= 2:
        chunks.append(chunk)
    return chunks


def _build_route_graph(debug: bool = False) -> dict[str, Any]:
    """nodes: cell -> (lat, lon); adj: cell -> list of
    (nbr, weight, length_m, category, coords, reversed?, name). Largest
    connected component only, so off-island termini snap to routable nodes."""
    nodes: dict = {}
    adj: dict = {}
    street_nodes: set = set()
    path_nodes: set = set()

    def _node(lon: float, lat: float):
        cell = _grid_node(lat, lon)
        if cell not in nodes:
            nodes[cell] = (lat, lon)
            adj[cell] = []
        return cell

    bike_factors = MODE_FACTORS['bike']
    for coords, category, name in _route_source_rows(debug=debug):
        factor = bike_factors.get(category, MODE_DEFAULT_FACTOR['bike'])
        is_path = category in ('srt', 'bike-lane')
        for chunk in _subdivide(coords):
            length_m = sum(
                _equirect_m(a[1], a[0], b[1], b[0])
                for a, b in zip(chunk, chunk[1:])
            )
            if length_m <= 0:
                continue
            u = _node(chunk[0][0], chunk[0][1])
            v = _node(chunk[-1][0], chunk[-1][1])
            (path_nodes if is_path else street_nodes).update((u, v))
            if u == v:
                continue  # self-loop after snapping
            weight = length_m * factor
            # Keep the cheapest edge per node pair (bike lane painted on a
            # stressful street should win).
            existing = next(
                (e for e in adj[u] if e[0] == v), None,
            )
            if existing is not None and existing[1] <= weight:
                continue
            if existing is not None:
                adj[u] = [e for e in adj[u] if e[0] != v]
                adj[v] = [e for e in adj[v] if e[0] != u]
            adj[u].append((v, weight, length_m, category, chunk, False, name))
            adj[v].append((u, weight, length_m, category, chunk, True, name))

    def _add_connector(u, v):
        d = _equirect_m(*nodes[u], *nodes[v])
        coords = [
            [nodes[u][1], nodes[u][0]],
            [nodes[v][1], nodes[v][0]],
        ]
        length = max(d, 1.0)
        adj[u].append((v, length, length, 'connector', coords, False, None))
        adj[v].append((u, length, length, 'connector', coords, True, None))

    def _spatial_hash(cells, cell_m):
        cell_lat = cell_m / M_PER_DEG_LAT
        cell_lon = cell_lat / _COSLAT
        spatial: dict = {}
        for cell in cells:
            lat, lon = nodes[cell]
            key = (int(lat / cell_lat), int(lon / cell_lon))
            spatial.setdefault(key, []).append(cell)
        return spatial, cell_lat, cell_lon

    def _nearest_in_hash(spatial, cell_lat, cell_lon, lat, lon, want=None):
        key = (int(lat / cell_lat), int(lon / cell_lon))
        best, best_d = None, None
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for v in spatial.get((key[0] + dy, key[1] + dx), []):
                    if want is not None and not want(v):
                        continue
                    d = _equirect_m(lat, lon, *nodes[v])
                    if best_d is None or d < best_d:
                        best, best_d = v, d
        return best, best_d

    # Junctions: the trail (and off-street greenway lanes) crosses dozens of
    # streets without ever sharing an endpoint with one -- left alone it's a
    # long tube with one door, and routes can't get on or off. Connect every
    # trail/lane-only node to its nearest street node (component-agnostic).
    junction_targets = street_nodes - path_nodes
    if junction_targets:
        spatial, cl, cn = _spatial_hash(junction_targets, ROUTE_CONNECT_M)
        for u in path_nodes - street_nodes:
            best, best_d = _nearest_in_hash(spatial, cl, cn, *nodes[u])
            if best is not None and best_d <= ROUTE_CONNECT_M:
                _add_connector(u, best)

    def _components() -> list[set]:
        seen: set = set()
        comps = []
        for start in adj:
            if start in seen:
                continue
            comp = {start}
            queue = [start]
            seen.add(start)
            while queue:
                cur = queue.pop()
                for e in adj[cur]:
                    if e[0] not in seen:
                        seen.add(e[0])
                        comp.add(e[0])
                        queue.append(e[0])
            comps.append(comp)
        return sorted(comps, key=len, reverse=True)

    # The SRT (and some bike lanes) never share endpoints with the street
    # grid: the trail breaks at every road crossing, so it arrives as many
    # disconnected fragments. For THROUGH-travel the fragments must bridge to
    # each other (and to the streets) at every close approach -- one
    # connector per component is a dead end. So: every node gets a straight
    # connector to its nearest node in a DIFFERENT component, if within
    # ROUTE_STITCH_M.
    comps = _components()
    if comps and len(comps) > 1:
        comp_of: dict = {}
        for i, comp in enumerate(comps):
            for c in comp:
                comp_of[c] = i
        spatial, cl, cn = _spatial_hash(
            list(adj), max(ROUTE_STITCH_M, ROUTE_GRID_M),
        )
        for u in list(adj):
            best, best_d = _nearest_in_hash(
                spatial, cl, cn, *nodes[u],
                want=lambda v, _u=u: comp_of[v] != comp_of[_u],
            )
            if best is not None and best_d <= ROUTE_STITCH_M:
                _add_connector(u, best)

    # Keep the largest component (recomputed after stitching).
    comps = _components()
    best_comp = comps[0] if comps else set()
    nodes = {c: nodes[c] for c in best_comp}
    adj = {c: adj[c] for c in best_comp}
    return {'epoch': time.time(), 'nodes': nodes, 'adj': adj}


def _get_route_graph(debug: bool = False) -> dict[str, Any]:
    with _GRAPH_LOCK:
        if (
            _GRAPH['nodes'] is None
            or (time.time() - _GRAPH['epoch']) > _GRAPH_TTL_SECONDS
        ):
            info("Building low-stress routing graph...")
            built = _build_route_graph(debug=debug)
            _GRAPH.update(built)
            info(
                f"Routing graph: {len(built['nodes'])} nodes, "
                f"{sum(len(v) for v in built['adj'].values()) // 2} edges."
            )
    return _GRAPH


def _nearest_node(graph: dict, lat: float, lon: float):
    best, best_d = None, None
    for cell, (nlat, nlon) in graph['nodes'].items():
        d = _equirect_m(lat, lon, nlat, nlon)
        if best_d is None or d < best_d:
            best, best_d = cell, d
    return best, best_d


def _astar(graph: dict, start, goal, mode: str = 'bike'):
    """Returns list of (node, edge) from start to goal, or None. edge is the
    adjacency tuple taken to arrive at node (None for start). Edge weights are
    recomputed per mode from length x category factor (the stored weight is
    the bike one, kept for stats/dedup)."""
    import heapq

    nodes = graph['nodes']
    adj = graph['adj']
    glat, glon = nodes[goal]
    factors = MODE_FACTORS[mode]
    default_factor = MODE_DEFAULT_FACTOR[mode]
    min_factor = min(min(factors.values()), 1.0)  # connectors weigh 1.0

    def h(cell):
        lat, lon = nodes[cell]
        return _equirect_m(lat, lon, glat, glon) * min_factor

    def edge_weight(edge) -> float:
        category = edge[3]
        if category == 'connector':
            return edge[2]
        return edge[2] * factors.get(category, default_factor)

    dist = {start: 0.0}
    prev: dict = {start: (None, None)}
    heap = [(h(start), 0.0, start)]
    visited: set = set()
    while heap:
        _f, g, cur = heapq.heappop(heap)
        if cur in visited:
            continue
        visited.add(cur)
        if cur == goal:
            path = []
            node = goal
            while node is not None:
                parent, edge = prev[node]
                path.append((node, edge))
                node = parent
            return list(reversed(path))
        for edge in adj[cur]:
            nbr, weight = edge[0], edge_weight(edge)
            ng = g + weight
            if nbr not in dist or ng < dist[nbr]:
                dist[nbr] = ng
                prev[nbr] = (cur, edge)
                heapq.heappush(heap, (ng + h(nbr), ng, nbr))
    return None


#: Bearing change (degrees) at a junction -> maneuver type. Ordered by the
#: upper bound of |delta|; the sign picks left vs. right.
TURN_BANDS = (
    (20, 'straight'),
    (50, 'slight'),
    (125, 'turn'),
    (165, 'sharp'),
)
COMPASS = ('north', 'northeast', 'east', 'southeast',
           'south', 'southwest', 'west', 'northwest')
#: Merge a leg into the current step below this bearing change (unless the
#: street name changes) so gentle curves don't generate maneuvers.
STEP_TURN_MIN_DEG = 22.0
#: Same street (or an unnamed/connector leg): only a real corner splits it.
STEP_SAME_STREET_TURN_DEG = 60.0
#: Steps shorter than this (meters) are folded into the previous one.
STEP_MIN_M = 25.0


def _bearing(a: list, b: list) -> float:
    """Compass bearing in degrees from [lon, lat] `a` to `b`."""
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlon = math.radians(b[0] - a[0])
    y = math.sin(dlon) * math.cos(lat2)
    x = (
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    )
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _maneuver(delta: float) -> str:
    """Signed bearing change (-180..180) -> maneuver key."""
    side = 'left' if delta < 0 else 'right'
    mag = abs(delta)
    for bound, kind in TURN_BANDS:
        if mag < bound:
            if kind == 'straight':
                return 'straight'
            return f'{kind}-{side}' if kind != 'turn' else side
    return 'uturn'


def _titleize(name: str | None) -> str | None:
    """GIS street names are SHOUTED; instructions shouldn't be."""
    if not name:
        return None
    return name.title() if name.isupper() else name


def _instruction(
    maneuver: str,
    name: str | None,
    bearing: float,
    prev_name: str | None = None,
) -> str:
    name = _titleize(name)
    onto = f' onto {name}' if name else ''
    if maneuver == 'depart':
        heading = COMPASS[int((bearing + 22.5) % 360 // 45)]
        return f'Head {heading}' + (f' on {name}' if name else '')
    if maneuver == 'arrive':
        return 'Arrive at your destination'
    if maneuver == 'straight':
        if not name:
            return 'Continue straight'
        return f'Continue onto {name}' if name != _titleize(prev_name) else f'Continue on {name}'
    if maneuver == 'uturn':
        return f'Make a U-turn{onto}'
    words = {
        'left': 'Turn left',
        'right': 'Turn right',
        'slight-left': 'Bear left',
        'slight-right': 'Bear right',
        'sharp-left': 'Sharp left',
        'sharp-right': 'Sharp right',
    }
    return words.get(maneuver, 'Continue') + onto


def _build_steps(
    legs: list[dict],
    coordinates: list,
    speed_m_s: float = 4.2,
) -> list[dict]:
    """Collapse routed legs into turn-by-turn steps.

    A new step starts when the street name changes or the bearing swings more
    than [STEP_TURN_MIN_DEG]; everything else accumulates into the step in
    progress, the way Google/Komoot report "continue for 0.4 mi".
    """
    steps: list[dict] = []
    prev_out_bearing: float | None = None

    for leg in legs:
        coords = leg['coords']
        if len(coords) < 2:
            continue
        in_bearing = _bearing(coords[0], coords[1])
        out_bearing = _bearing(coords[-2], coords[-1])
        if prev_out_bearing is None:
            maneuver, delta = 'depart', 0.0
        else:
            delta = ((in_bearing - prev_out_bearing + 540.0) % 360.0) - 180.0
            maneuver = _maneuver(delta)

        # Connector edges are synthetic gap-crossers with no street name; they
        # belong to whatever step they interrupt, not to a maneuver of their own.
        # Unnamed legs likewise continue whatever the rider is already on.
        continues = bool(steps) and (
            steps[-1]['name'] == _titleize(leg['name'])
            or leg['category'] == 'connector'
            or not leg['name']
        )
        # A street that curves stays one step; only a real corner splits it.
        limit = STEP_SAME_STREET_TURN_DEG if continues else STEP_TURN_MIN_DEG
        if steps and continues and abs(delta) < limit:
            steps[-1]['distance_m'] += leg['length_m']
        else:
            steps.append({
                'maneuver': maneuver,
                'name': _titleize(leg['name']),
                'category': leg['category'],
                'instruction': _instruction(
                    maneuver, leg['name'], in_bearing,
                    prev_name=steps[-1]['name'] if steps else None,
                ),
                'distance_m': leg['length_m'],
                'start_index': leg['start_index'],
                'location': list(coords[0]),
                'bearing': round(in_bearing, 1),
            })
        prev_out_bearing = out_bearing

    # Fold away hops too short to announce ("in 40 ft, turn left, then turn
    # right" is worse than one instruction).
    merged: list[dict] = []
    for step in steps:
        if merged and step['distance_m'] < STEP_MIN_M and step['maneuver'] != 'depart':
            merged[-1]['distance_m'] += step['distance_m']
            continue
        merged.append(step)
    steps = merged

    # A hair-length depart leg (origin -> snapped node) is noise; promote the
    # first real step to the departure instead.
    if (
        len(steps) > 1
        and steps[0]['maneuver'] == 'depart'
        and steps[0]['distance_m'] < STEP_MIN_M
    ):
        head, nxt = steps.pop(0), steps[0]
        nxt['distance_m'] += head['distance_m']
        nxt['start_index'] = head['start_index']
        nxt['location'] = head['location']
        nxt['maneuver'] = 'depart'
        nxt['instruction'] = _instruction('depart', nxt['name'], nxt['bearing'])

    if coordinates:
        steps.append({
            'maneuver': 'arrive',
            'name': None,
            'category': None,
            'instruction': _instruction('arrive', None, 0.0),
            'distance_m': 0.0,
            'start_index': len(coordinates) - 1,
            'location': list(coordinates[-1]),
        })
    for step in steps:
        step['distance_m'] = round(step['distance_m'], 1)
        step['duration_min'] = round(
            step['distance_m'] / speed_m_s / 60, 2
        )
    return steps


def _route(
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    mode: str = 'bike',
) -> dict[str, Any]:
    """Compute a bike or walk route; raises ValueError with a user-facing
    message when no route is possible."""
    graph = _get_route_graph()
    if not graph['nodes']:
        raise ValueError("Routing network is unavailable.")

    start, start_d = _nearest_node(graph, from_lat, from_lon)
    goal, goal_d = _nearest_node(graph, to_lat, to_lon)
    if start_d > ROUTE_SNAP_MAX_M or goal_d > ROUTE_SNAP_MAX_M:
        noun = MODE_NETWORK_NOUN.get(mode, 'routable')
        raise ValueError(f"No {noun} network near that point.")

    legs: list[dict] = []
    if start == goal:
        nlat, nlon = graph['nodes'][start]
        coordinates = [[from_lon, from_lat], [nlon, nlat], [to_lon, to_lat]]
        distance_m = start_d + goal_d
        breakdown: dict[str, float] = {}
        legs.append({
            'coords': coordinates,
            'name': None,
            'category': None,
            'length_m': distance_m,
            'start_index': 0,
        })
    else:
        path = _astar(graph, start, goal, mode=mode)
        if path is None:
            raise ValueError("Couldn't find a connected route.")
        coordinates = [[from_lon, from_lat]]
        distance_m = start_d + goal_d
        breakdown = {}
        for _node, edge in path:
            if edge is None:
                continue
            _nbr, _w, length_m, category, coords, is_reversed, name = edge
            seg = list(reversed(coords)) if is_reversed else list(coords)
            start_index = max(len(coordinates) - 1, 0)
            leg_coords = [coordinates[-1]] + seg if coordinates else list(seg)
            if coordinates and coordinates[-1] == seg[0]:
                seg = seg[1:]
                leg_coords = [coordinates[-1]] + seg
            coordinates.extend(seg)
            distance_m += length_m
            key = str(category or 'unknown')
            breakdown[key] = breakdown.get(key, 0.0) + length_m
            legs.append({
                'coords': leg_coords,
                'name': name,
                'category': category,
                'length_m': length_m,
                'start_index': start_index,
            })
        coordinates.append([to_lon, to_lat])

    speed = MODE_SPEED_M_S[mode]
    return {
        'type': 'Feature',
        'geometry': {'type': 'LineString', 'coordinates': coordinates},
        'properties': {
            'mode': mode,
            'distance_m': round(distance_m, 1),
            'distance_mi': round(distance_m / 1609.344, 2),
            'duration_min': round(distance_m / speed / 60, 1),
            'stress_breakdown': {k: round(v, 1) for k, v in breakdown.items()},
            'from_snap_m': round(start_d, 1),
            'to_snap_m': round(goal_d, 1),
            'steps': _build_steps(legs, coordinates, speed_m_s=speed),
        },
    }


# ------------------------------------------------------------------ transit

_TRANSIT: dict[str, Any] = {'epoch': 0.0, 'stops': None, 'shapes': None}
_TRANSIT_LOCK = threading.Lock()
_TRANSIT_TTL_SECONDS = 24 * 60 * 60


def _get_transit_data() -> dict[str, Any]:
    """Greenlink stops + route shapes, cached in-process.

    stops: list of {name, lat, lon, routes: set of short names}
    shapes: list of {route, long_name, color, coords: [[lon,lat],...],
                     cumulative: [m from start]}
    """
    with _TRANSIT_LOCK:
        if (
            _TRANSIT['stops'] is not None
            and (time.time() - _TRANSIT['epoch']) < _TRANSIT_TTL_SECONDS
        ):
            return _TRANSIT
        import json

        conn = mrsm.get_connector('sql:bwg')
        stops = []
        df = conn.read('SELECT "name", "lat", "lon", "routes" FROM "transit"."stops"')
        for rec in df.to_dict(orient='records'):
            routes = {
                r.strip()
                for r in str(rec.get('routes') or '').split(',')
                if r.strip()
            }
            if not routes:
                continue
            stops.append({
                'name': rec['name'],
                'lat': float(rec['lat']),
                'lon': float(rec['lon']),
                'routes': routes,
            })

        shapes = []
        df = conn.read('''
            SELECT "short_name", "long_name", "color",
                   ST_AsGeoJSON("geometry", 5) AS "gj"
            FROM "transit"."route_shapes"
        ''')
        for rec in df.to_dict(orient='records'):
            gj = rec.get('gj')
            if not gj:
                continue
            coords = json.loads(gj).get('coordinates') or []
            if len(coords) < 2:
                continue
            cumulative = [0.0]
            for a, b in zip(coords, coords[1:]):
                cumulative.append(
                    cumulative[-1] + _equirect_m(a[1], a[0], b[1], b[0])
                )
            shapes.append({
                'route': str(rec.get('short_name') or ''),
                'long_name': str(rec.get('long_name') or ''),
                'color': rec.get('color'),
                'coords': coords,
                'cumulative': cumulative,
            })
        _TRANSIT.update({'epoch': time.time(), 'stops': stops, 'shapes': shapes})
        return _TRANSIT


def _project_on_shape(shape: dict, lat: float, lon: float) -> tuple[float, float, int, float]:
    """(meters along shape, offset meters, segment index, t) of the closest
    point on the shape to (lat, lon)."""
    coords = shape['coords']
    best = (0.0, float('inf'), 0, 0.0)
    for i in range(len(coords) - 1):
        ax, ay = coords[i][0] * _COSLAT, coords[i][1]
        bx, by = coords[i + 1][0] * _COSLAT, coords[i + 1][1]
        px, py = lon * _COSLAT, lat
        dx, dy = bx - ax, by - ay
        denom = dx * dx + dy * dy
        t = 0.0 if denom == 0 else ((px - ax) * dx + (py - ay) * dy) / denom
        t = max(0.0, min(1.0, t))
        cx, cy = ax + dx * t, ay + dy * t
        off = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 * M_PER_DEG_LAT
        if off < best[1]:
            seg_len = shape['cumulative'][i + 1] - shape['cumulative'][i]
            best = (shape['cumulative'][i] + seg_len * t, off, i, t)
    return best


def _shape_slice(shape: dict, a: tuple, b: tuple) -> list[list]:
    """Shape coords between projections a and b (from _project_on_shape)."""
    coords = shape['coords']

    def _point(proj):
        _dist, _off, i, t = proj
        ax, ay = coords[i]
        bx, by = coords[i + 1]
        return [ax + (bx - ax) * t, ay + (by - ay) * t]

    out = [_point(a)]
    out.extend(coords[a[2] + 1:b[2] + 1])
    out.append(_point(b))
    return [c for i, c in enumerate(out) if i == 0 or c != out[i - 1]]


def _walk_or_direct(
    from_lat, from_lon, to_lat, to_lon, toward: str = 'your stop',
) -> dict[str, Any]:
    """Walk route, falling back to a straight line when off-network."""
    try:
        return _route(from_lat, from_lon, to_lat, to_lon, mode='walk')
    except ValueError:
        coords = [[from_lon, from_lat], [to_lon, to_lat]]
        dist = _equirect_m(from_lat, from_lon, to_lat, to_lon)
        return {
            'type': 'Feature',
            'geometry': {'type': 'LineString', 'coordinates': coords},
            'properties': {
                'distance_m': round(dist, 1),
                'duration_min': round(dist / MODE_SPEED_M_S['walk'] / 60, 1),
                'steps': [{
                    'maneuver': 'depart',
                    'name': None,
                    'category': None,
                    'instruction': f'Walk toward {toward}',
                    'distance_m': round(dist, 1),
                    'duration_min': round(dist / MODE_SPEED_M_S['walk'] / 60, 2),
                    'start_index': 0,
                    'location': coords[0],
                    'bearing': 0.0,
                }],
            },
        }


def _route_transit(
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
) -> dict[str, Any]:
    """Walk -> ride a Greenlink route -> walk. No schedules yet (stop_times
    isn't ingested), so the wait is a flat estimate and the ride follows the
    route shape between the boarding and alighting stops."""
    data = _get_transit_data()
    stops, shapes = data['stops'], data['shapes']
    if not stops or not shapes:
        raise ValueError("Transit network is unavailable.")

    def _near(lat, lon):
        out = []
        for s in stops:
            d = _equirect_m(lat, lon, s['lat'], s['lon'])
            if d <= TRANSIT_WALK_MAX_M:
                out.append((d, s))
        out.sort(key=lambda x: x[0])
        return out[:25]

    near_from = _near(from_lat, from_lon)
    near_to = _near(to_lat, to_lon)
    if not near_from or not near_to:
        raise ValueError("No bus stops within walking distance.")

    shapes_by_route: dict[str, list] = {}
    for sh in shapes:
        shapes_by_route.setdefault(sh['route'], []).append(sh)

    proj_cache: dict = {}

    def _proj(shape_i, s):
        key = (shape_i, id(s))
        if key not in proj_cache:
            proj_cache[key] = _project_on_shape(shapes[shape_i], s['lat'], s['lon'])
        return proj_cache[key]

    shape_index = {id(sh): i for i, sh in enumerate(shapes)}
    walk_speed = MODE_SPEED_M_S['walk']
    best = None  # (total_s, d1, s1, d2, s2, shape, p1, p2)
    for d1, s1 in near_from:
        for d2, s2 in near_to:
            shared = s1['routes'] & s2['routes']
            if not shared or s1 is s2:
                continue
            for route in shared:
                for sh in shapes_by_route.get(route, []):
                    i = shape_index[id(sh)]
                    p1 = _proj(i, s1)
                    p2 = _proj(i, s2)
                    if p1[1] > TRANSIT_STOP_SNAP_M or p2[1] > TRANSIT_STOP_SNAP_M:
                        continue
                    ride_m = p2[0] - p1[0]
                    if ride_m < TRANSIT_MIN_RIDE_M:
                        continue
                    total_s = (
                        (d1 + d2) / walk_speed
                        + ride_m / TRANSIT_BUS_SPEED_M_S
                        + TRANSIT_WAIT_MIN * 60
                    )
                    if best is None or total_s < best[0]:
                        best = (total_s, d1, s1, d2, s2, sh, p1, p2)

    if best is None:
        raise ValueError(
            "No direct bus route between those points — try walk or bike."
        )
    _total_s, _d1, s1, _d2, s2, shape, p1, p2 = best

    walk1 = _walk_or_direct(from_lat, from_lon, s1['lat'], s1['lon'])
    walk2 = _walk_or_direct(
        s2['lat'], s2['lon'], to_lat, to_lon, toward='your destination',
    )
    ride_coords = _shape_slice(shape, p1, p2)
    ride_m = p2[0] - p1[0]

    coordinates: list = []
    steps: list[dict] = []

    def _append_leg(feature, drop_arrive: bool):
        offset = max(len(coordinates) - 1, 0)
        coords = feature['geometry']['coordinates']
        if coordinates and coordinates[-1] == coords[0]:
            coords = coords[1:]
        else:
            offset = len(coordinates)
        coordinates.extend(coords)
        for step in feature['properties']['steps']:
            if drop_arrive and step['maneuver'] == 'arrive':
                continue
            step = dict(step)
            step['start_index'] = step['start_index'] + offset
            steps.append(step)

    _append_leg(walk1, drop_arrive=True)

    route_name = f"Route {shape['route']}".strip()
    long_name = _titleize(shape['long_name'])
    board_index = max(len(coordinates) - 1, 0)
    steps.append({
        'maneuver': 'board',
        'name': route_name,
        'category': 'transit',
        'instruction': (
            f"Board Greenlink {route_name}"
            + (f' ({long_name})' if long_name else '')
            + f" at {s1['name']}"
        ),
        'distance_m': 0.0,
        'duration_min': round(TRANSIT_WAIT_MIN, 1),
        'start_index': board_index,
        'location': [s1['lon'], s1['lat']],
        'bearing': 0.0,
    })
    ride_start_index = len(coordinates) - 1 if coordinates else 0
    if coordinates and coordinates[-1] == ride_coords[0]:
        coordinates.extend(ride_coords[1:])
    else:
        ride_start_index = len(coordinates)
        coordinates.extend(ride_coords)
    steps.append({
        'maneuver': 'ride',
        'name': route_name,
        'category': 'transit',
        'instruction': f"Ride {route_name} to {s2['name']}",
        'distance_m': round(ride_m, 1),
        'duration_min': round(ride_m / TRANSIT_BUS_SPEED_M_S / 60, 1),
        'start_index': ride_start_index,
        'location': [s1['lon'], s1['lat']],
        'bearing': 0.0,
    })
    steps.append({
        'maneuver': 'alight',
        'name': s2['name'],
        'category': 'transit',
        'instruction': f"Get off at {s2['name']}",
        'distance_m': 0.0,
        'duration_min': 0.0,
        'start_index': max(len(coordinates) - 1, 0),
        'location': [s2['lon'], s2['lat']],
        'bearing': 0.0,
    })
    _append_leg(walk2, drop_arrive=False)
    if not steps or steps[-1]['maneuver'] != 'arrive':
        steps.append({
            'maneuver': 'arrive',
            'name': None,
            'category': None,
            'instruction': 'Arrive at your destination',
            'distance_m': 0.0,
            'duration_min': 0.0,
            'start_index': max(len(coordinates) - 1, 0),
            'location': list(coordinates[-1]),
        })

    walk_m = (
        walk1['properties']['distance_m'] + walk2['properties']['distance_m']
    )
    duration_min = (
        walk1['properties']['duration_min']
        + walk2['properties']['duration_min']
        + ride_m / TRANSIT_BUS_SPEED_M_S / 60
        + TRANSIT_WAIT_MIN
    )
    distance_m = walk_m + ride_m
    return {
        'type': 'Feature',
        'geometry': {'type': 'LineString', 'coordinates': coordinates},
        'properties': {
            'mode': 'transit',
            'distance_m': round(distance_m, 1),
            'distance_mi': round(distance_m / 1609.344, 2),
            'duration_min': round(duration_min, 1),
            'walk_m': round(walk_m, 1),
            'ride_m': round(ride_m, 1),
            'route': shape['route'],
            'route_long_name': shape['long_name'],
            'route_color': shape.get('color'),
            'board_stop': s1['name'],
            'alight_stop': s2['name'],
            'wait_min': TRANSIT_WAIT_MIN,
            'steps': steps,
        },
    }


# User feedback on any map feature (bus stop, sidewalk segment, bike lane, ...).
FEEDBACK_PIPE: mrsm.Pipe = mrsm.Pipe(
    'app', 'feedback', 'MapLayers',
    instance='sql:bwg',
    parameters={
        'autotime': True,
        'schema': 'MapLayers',
        'target': 'layer_feedback',
        'columns': {
            'datetime': 'ts',
            'id': 'id',
        },
        'dtypes': {
            'ts': 'datetime',
            'id': 'string',
            'layer': 'string',
            'name': 'string',
            'lat': 'float',
            'lon': 'float',
            'props': 'string',
            'feedback': 'string',
            'photo_filename': 'string',
            'ip': 'string',
            'user_agent': 'string',
        },
    },
)


def _photos_dir():
    """Directory where uploaded photos are stored (created on demand)."""
    from pathlib import Path
    from meerschaum.config.paths import ROOT_DIR_PATH
    photos_dir = Path(ROOT_DIR_PATH) / 'uploads' / 'map-layers'
    photos_dir.mkdir(parents=True, exist_ok=True)
    return photos_dir


def _layer_index() -> list[dict[str, Any]]:
    return [
        {
            'id': layer_id,
            'label': layer['label'],
            'kind': layer['kind'],
            'url': f'/map-layers/{layer_id}.geojson',
            'props': list(layer.get('props', {})),
            **{
                key: layer[key]
                for key in ('color', 'color_by', 'icon')
                if key in layer
            },
        }
        for layer_id, layer in LAYERS.items()
    ]


@make_action
def export_map_layers(debug: bool = False, **kwargs) -> mrsm.SuccessTuple:
    """Run `mrsm export map_layers` to pregenerate the app layer files."""
    import json

    output_dir = _output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    num_successes = 0
    for layer_id in LAYERS:
        info(f"Exporting layer '{layer_id}'...")
        try:
            json_str = _build_layer_geojson(layer_id, debug=debug)
        except Exception as e:
            return False, f"Failed to export layer '{layer_id}':\n{e}"
        if json_str is None:
            continue

        layer_path = output_dir / f'{layer_id}.geojson'
        with open(layer_path, 'w+') as f:
            f.write(json_str)
        mrsm.pprint((True, f"Wrote file '{layer_path}'."))
        num_successes += 1

    with open(output_dir / 'index.json', 'w+') as f:
        json.dump({'layers': _layer_index()}, f)

    if num_successes == 0:
        return False, "Did not export any layers."

    return True, f"Successfully exported {num_successes} of {len(LAYERS)} layers."


@api_plugin
def init_app(app):
    """Register the map-layers HTTP routes on the Meerschaum API app."""
    import uuid
    import shutil
    from pathlib import Path
    from fastapi import Form, File, UploadFile, Request, Query
    from fastapi.responses import JSONResponse, FileResponse, Response
    from starlette.middleware.gzip import GZipMiddleware

    # App-wide (all bwg.mrsm.io routes): geojson payloads gzip ~8-10x.
    if not any(m.cls is GZipMiddleware for m in app.user_middleware):
        app.add_middleware(GZipMiddleware, minimum_size=2048)

    @app.get('/map-layers/index.json')
    def map_layers_index():
        return JSONResponse({'layers': _layer_index()})

    @app.get('/map-layers/{layer_id}.geojson')
    def map_layer_geojson(
        layer_id: str,
        bbox: str = None,
        zoom: int = 13,
    ):
        if layer_id not in LAYERS:
            return JSONResponse({'error': f"Unknown layer '{layer_id}'."}, status_code=404)

        # Viewport query: ?bbox=minlon,minlat,maxlon,maxlat&zoom=14
        if bbox:
            try:
                minlon, minlat, maxlon, maxlat = (float(v) for v in bbox.split(','))
            except Exception:
                return JSONResponse({'error': 'Invalid bbox.'}, status_code=400)
            try:
                json_str = _build_bbox_geojson(
                    layer_id, minlon, minlat, maxlon, maxlat, zoom,
                )
            except Exception as e:
                return JSONResponse({'error': str(e)}, status_code=500)
            return Response(json_str, media_type='application/geo+json')

        pregenerated = _output_dir() / f'{layer_id}.geojson'
        if pregenerated.exists():
            return FileResponse(pregenerated, media_type='application/geo+json')

        cached = _CACHE.get(layer_id)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
            return Response(cached[1], media_type='application/geo+json')

        try:
            json_str = _build_layer_geojson(layer_id)
        except Exception as e:
            return JSONResponse({'error': str(e)}, status_code=500)
        if json_str is None:
            return JSONResponse({'error': f"No data for layer '{layer_id}'."}, status_code=404)

        _CACHE[layer_id] = (time.time(), json_str)
        return Response(json_str, media_type='application/geo+json')

    @app.get('/map-layers/route')
    def map_layers_route(
        to: str = '',
        from_: str = Query('', alias='from'),
        mode: str = 'bike',
    ):
        # Sync def on purpose: FastAPI runs it in the threadpool, so the
        # multi-second first-call graph build never blocks the event loop.
        def _parse_latlon(value: str):
            lat_s, lon_s = value.split(',', 1)
            return float(lat_s), float(lon_s)

        mode = (mode or 'bike').strip().lower()
        if mode not in ('bike', 'walk', 'transit'):
            return JSONResponse(
                {'error': "Expected mode=bike, walk or transit."},
                status_code=400,
            )
        try:
            from_lat, from_lon = _parse_latlon(from_)
            to_lat, to_lon = _parse_latlon(to)
        except Exception:
            return JSONResponse(
                {'error': "Expected ?from=lat,lon&to=lat,lon."},
                status_code=400,
            )
        if (from_lat, from_lon) == (to_lat, to_lon):
            return JSONResponse(
                {'error': "Start and destination are the same point."},
                status_code=400,
            )
        try:
            if mode == 'transit':
                feature = _route_transit(from_lat, from_lon, to_lat, to_lon)
            else:
                feature = _route(from_lat, from_lon, to_lat, to_lon, mode=mode)
        except ValueError as e:
            return JSONResponse({'error': str(e)}, status_code=422)
        except Exception as e:
            warn(f"Routing failed: {e}")
            return JSONResponse({'error': 'Routing failed.'}, status_code=500)
        return JSONResponse(feature)

    def _srt_gaps(graph, srt_adj):
        """Min pairwise distance (m) between srt-only components."""
        seen: set = set()
        comps = []
        for start in srt_adj:
            if start in seen:
                continue
            comp = [start]
            seen.add(start)
            queue = [start]
            while queue:
                cur = queue.pop()
                for nbr in srt_adj.get(cur, []):
                    if nbr not in seen:
                        seen.add(nbr)
                        comp.append(nbr)
                        queue.append(nbr)
            comps.append(comp)
        comps.sort(key=len, reverse=True)
        gaps = []
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                best = None
                for u in comps[i]:
                    for v in comps[j]:
                        d = _equirect_m(*graph['nodes'][u], *graph['nodes'][v])
                        if best is None or d < best:
                            best = d
                gaps.append({
                    'a': i, 'b': j,
                    'a_size': len(comps[i]), 'b_size': len(comps[j]),
                    'gap_m': round(best, 1),
                })
        gaps.sort(key=lambda g: g['gap_m'])
        return gaps[:15]

    @app.get('/map-layers/route-stats.json')
    def map_layers_route_stats():
        graph = _get_route_graph()
        counts: dict = {}
        length_m: dict = {}
        for edges in graph['adj'].values():
            for e in edges:
                if e[5]:  # count each undirected edge once
                    continue
                cat = str(e[3] or 'unknown')
                counts[cat] = counts.get(cat, 0) + 1
                length_m[cat] = length_m.get(cat, 0.0) + e[2]
        # Continuity check: components of the srt-only subgraph.
        srt_adj: dict = {}
        for u, edges in graph['adj'].items():
            for e in edges:
                if e[3] == 'srt':
                    srt_adj.setdefault(u, []).append(e[0])
        seen: set = set()
        srt_comps = []
        for start in srt_adj:
            if start in seen:
                continue
            comp = [start]
            seen.add(start)
            queue = [start]
            while queue:
                cur = queue.pop()
                for nbr in srt_adj.get(cur, []):
                    if nbr not in seen:
                        seen.add(nbr)
                        comp.append(nbr)
                        queue.append(nbr)
            srt_comps.append(len(comp))
        return JSONResponse({
            'nodes': len(graph['nodes']),
            'edges': sum(counts.values()),
            'edges_by_category': counts,
            'km_by_category': {k: round(v / 1000, 1) for k, v in length_m.items()},
            'srt_components': sorted(srt_comps, reverse=True)[:20],
            'srt_component_count': len(srt_comps),
            'srt_gaps_m': _srt_gaps(graph, srt_adj),
        })

    @app.get('/map-layers/search')
    def map_layers_search(q: str = '', limit: int = 12):
        q = (q or '').strip()
        limit = max(1, min(int(limit), 25))
        if len(q) < 2:
            return JSONResponse({'results': []})
        try:
            results = _search_local(q, limit)
        except Exception as e:
            warn(f"Local search failed for {q!r}: {e}")
            results = []
        if not results:
            try:
                results = _search_nominatim(q)
            except Exception:
                results = []
        return JSONResponse({'results': results})

    @app.get('/map-layers/road-info')
    def map_layers_road_info(lat: float, lon: float):
        """Nearest road segment + its owner contact info ("Who Owns The Roads" on tap)."""
        query = """
        WITH pt AS (
            SELECT ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 6570) AS geom
        )
        SELECT
            r."Name", r."Type", r."Owner", r."Email", r."Phone", r."Online Form",
            ST_Distance(r.geometry, pt.geom) AS distance_ft
        FROM "Roads".roads AS r, pt
        ORDER BY r.geometry <-> pt.geom
        LIMIT 1
        """
        try:
            conn = mrsm.get_connector('sql:bwg')
            df = conn.read(query, params={'lat': lat, 'lon': lon})
        except Exception as e:
            return JSONResponse({'error': str(e)}, status_code=500)
        if df is None or not len(df):
            return JSONResponse({'error': 'No road found.'}, status_code=404)
        row = df.iloc[0]

        def _val(key):
            v = row.get(key)
            return None if v is None or (isinstance(v, float) and v != v) or v == 'N/A' else v

        return JSONResponse({
            'name': _val('Name'),
            'type': _val('Type'),
            'owner': _val('Owner'),
            'email': _val('Email'),
            'phone': _val('Phone'),
            'online_form': _val('Online Form'),
            'distance_ft': round(float(row['distance_ft']), 1),
        })

    @app.post('/map-layers/feedback')
    async def submit_layer_feedback(
        request: Request,
        layer: str = Form(''),
        name: str = Form(''),
        lat: float = Form(None),
        lon: float = Form(None),
        props: str = Form(''),
        feedback: str = Form(''),
        photo: UploadFile = File(None),
    ):
        rec_id = uuid.uuid4().hex
        photo_filename = None
        if photo is not None and photo.filename:
            ext = Path(photo.filename).suffix or '.jpg'
            photo_filename = f'{rec_id}{ext}'
            with open(_photos_dir() / photo_filename, 'wb') as out:
                shutil.copyfileobj(photo.file, out)

        client = request.client
        FEEDBACK_PIPE.sync(
            [{
                'id': rec_id,
                'layer': layer or None,
                'name': name or None,
                'lat': lat,
                'lon': lon,
                'props': props or None,
                'feedback': feedback or None,
                'photo_filename': photo_filename,
                'ip': client.host if client else None,
                'user_agent': request.headers.get('user-agent'),
            }],
            blocking=False,
        )
        return JSONResponse({'ok': True, 'id': rec_id})

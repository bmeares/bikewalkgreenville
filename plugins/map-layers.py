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

__version__ = '0.1.1'

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
        'pipe': ('plugin:city-gis', 'BusRoutes'),
        'props': {'dir_id': 'dir_id'},
        'tolerance_m': 5,
        'color': '#7B1FA2',
    },
    'bus-stops': {
        'label': 'Bus Stops',
        'kind': 'point',
        'pipe': ('plugin:city-gis', 'BusStops'),
        'props': {'name': 'NAME'},
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
        INITCAP("NAME") AS "label",
        'Bus stop' AS "sublabel",
        ST_Y(ST_Transform("geometry", 4326)) AS "lat",
        ST_X(ST_Transform("geometry", 4326)) AS "lon",
        'bus-stops' AS "kind",
        ("NAME" ILIKE :qp ESCAPE '\') AS "prefix"
    FROM "city"."BusStops"
    WHERE "NAME" ILIKE :q ESCAPE '\'
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
# Low-stress routing
#
# Feasible-path finder biased toward the Swamp Rabbit Trail, bike lanes, and
# low-stress (PCC LTS) streets -- not turn-by-turn wayfinding. The graph is
# built lazily from PostGIS, cached in-process, and traversed with A*.
# =========================================================================

#: Edge weight multiplier per category: trail < bike lane < LTS levels.
ROUTE_FACTORS = {
    'srt': 0.4,
    'bike-lane': 0.4,
    'L': 1.0,
    'ML': 1.3,
    'M': 2.5,
    'MH': 6.0,
    'H': 12.0,
}
ROUTE_DEFAULT_FACTOR = 2.5
ROUTE_MIN_FACTOR = min(ROUTE_FACTORS.values())  # admissible A* heuristic
ROUTE_SNAP_MAX_M = 400      # reject termini farther than this from the network
ROUTE_SUBDIVIDE_M = 120.0   # split long lines so they're enterable mid-way
ROUTE_GRID_M = 12.0         # endpoint snap cell (stitches segment breaks)
ROUTE_STITCH_M = 60.0       # max connector length between components
ROUTE_CONNECT_M = 120.0     # max trail/lane -> street junction connector
ROUTE_SPEED_M_S = 4.2       # ~15 km/h casual cycling

M_PER_DEG_LAT = 111320.0
_COSLAT = math.cos(math.radians(34.85))  # Greenville-latitude lon scale

_GRAPH: dict[str, Any] = {'epoch': 0.0, 'nodes': None, 'adj': None}
_GRAPH_LOCK = threading.Lock()
_GRAPH_TTL_SECONDS = 24 * 60 * 60


def _equirect_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dx = (lon2 - lon1) * M_PER_DEG_LAT * _COSLAT
    dy = (lat2 - lat1) * M_PER_DEG_LAT
    return (dx * dx + dy * dy) ** 0.5


def _route_source_rows(debug: bool = False) -> list[tuple[list, str]]:
    """Pull (coords, category) rows for every routable segment.
    Geography-cast lengths sidestep per-CRS units; simplification preserves
    endpoints, so connectivity is unaffected."""
    import json

    sources = [
        ('bike-stress', None),       # category = stress_level per row
        ('bike-lanes', 'bike-lane'),
        ('srt', 'srt'),
    ]
    rows = []
    for layer_id, category in sources:
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
            {cat_col}
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
            for part in parts:
                if len(part) >= 2:
                    rows.append((part, rec.get('category')))
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
    (nbr, weight, length_m, category, coords, reversed?). Largest connected
    component only, so off-island termini snap to routable nodes."""
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

    for coords, category in _route_source_rows(debug=debug):
        factor = ROUTE_FACTORS.get(category, ROUTE_DEFAULT_FACTOR)
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
            adj[u].append((v, weight, length_m, category, chunk, False))
            adj[v].append((u, weight, length_m, category, chunk, True))

    def _add_connector(u, v):
        d = _equirect_m(*nodes[u], *nodes[v])
        coords = [
            [nodes[u][1], nodes[u][0]],
            [nodes[v][1], nodes[v][0]],
        ]
        length = max(d, 1.0)
        adj[u].append((v, length, length, 'connector', coords, False))
        adj[v].append((u, length, length, 'connector', coords, True))

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


def _astar(graph: dict, start, goal):
    """Returns list of (node, edge) from start to goal, or None. edge is the
    adjacency tuple taken to arrive at node (None for start)."""
    import heapq

    nodes = graph['nodes']
    adj = graph['adj']
    glat, glon = nodes[goal]

    def h(cell):
        lat, lon = nodes[cell]
        return _equirect_m(lat, lon, glat, glon) * ROUTE_MIN_FACTOR

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
            nbr, weight = edge[0], edge[1]
            ng = g + weight
            if nbr not in dist or ng < dist[nbr]:
                dist[nbr] = ng
                prev[nbr] = (cur, edge)
                heapq.heappush(heap, (ng + h(nbr), ng, nbr))
    return None


def _route(
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
) -> dict[str, Any]:
    """Compute a low-stress route; raises ValueError with a user-facing
    message when no route is possible."""
    graph = _get_route_graph()
    if not graph['nodes']:
        raise ValueError("Routing network is unavailable.")

    start, start_d = _nearest_node(graph, from_lat, from_lon)
    goal, goal_d = _nearest_node(graph, to_lat, to_lon)
    if start_d > ROUTE_SNAP_MAX_M or goal_d > ROUTE_SNAP_MAX_M:
        raise ValueError("No bikeable network near that point.")

    if start == goal:
        nlat, nlon = graph['nodes'][start]
        coordinates = [[from_lon, from_lat], [nlon, nlat], [to_lon, to_lat]]
        distance_m = start_d + goal_d
        breakdown: dict[str, float] = {}
    else:
        path = _astar(graph, start, goal)
        if path is None:
            raise ValueError("Couldn't find a connected low-stress route.")
        coordinates = [[from_lon, from_lat]]
        distance_m = start_d + goal_d
        breakdown = {}
        for _node, edge in path:
            if edge is None:
                continue
            _nbr, _w, length_m, category, coords, is_reversed = edge
            seg = list(reversed(coords)) if is_reversed else list(coords)
            if coordinates and coordinates[-1] == seg[0]:
                seg = seg[1:]
            coordinates.extend(seg)
            distance_m += length_m
            key = str(category or 'unknown')
            breakdown[key] = breakdown.get(key, 0.0) + length_m
        coordinates.append([to_lon, to_lat])

    return {
        'type': 'Feature',
        'geometry': {'type': 'LineString', 'coordinates': coordinates},
        'properties': {
            'distance_m': round(distance_m, 1),
            'duration_min': round(distance_m / ROUTE_SPEED_M_S / 60, 1),
            'stress_breakdown': {k: round(v, 1) for k, v in breakdown.items()},
            'from_snap_m': round(start_d, 1),
            'to_snap_m': round(goal_d, 1),
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
    ):
        # Sync def on purpose: FastAPI runs it in the threadpool, so the
        # multi-second first-call graph build never blocks the event loop.
        def _parse_latlon(value: str):
            lat_s, lon_s = value.split(',', 1)
            return float(lat_s), float(lon_s)

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
            feature = _route(from_lat, from_lon, to_lat, to_lon)
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
